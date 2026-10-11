from copy import deepcopy
from pathlib import Path
import contextlib
import hashlib
import io
import json
import struct
import tempfile
import unittest
from unittest.mock import patch

import automation as a
import media
from run_workflow import batch_run_ids, run_batch
from publisher_images import prepare_body
from test_automation import CONFIG, NOW, response


class BatchTests(unittest.TestCase):
    def test_structured_news_always_has_ordered_headings_and_four_markers(self):
        data=response();article=json.loads(data['output'][1]['content'][0]['text'])
        del article['body']
        article['sections']={'intro':'確認用の導入です。','conclusions':['確認用']*3,
            'explanations':[{'heading':'確認用'+str(i),'content':'検証用の本文です。'} for i in range(4)],
            'personal_view':'例えばという想定例。','faq':[{'question':'確認用？','answer':'確認用です。'}]*3,
            'summary':'確認用のまとめ。','about':'実ニュースではありません。'}
        article['images']=[{'number':i,'theme':'確認用','anchor':'','labels':['確認用'],'prompt':'one standalone image'} for i in range(5)]
        article.update(hashtags=['#確認'+str(i) for i in range(10)],threads='確認用です。どう思いますか？')
        data['output'][1]['content'][0]['text']=json.dumps(article)
        composed,evidence=a.extract_article(data)
        urls=a.validate_article(composed,evidence,{**CONFIG,'images':{'enabled':True}}, {'articles':[]},NOW)
        self.assertEqual(len(urls),2)
        self.assertEqual(composed['body'].count('【ここに図解'),4)
        self.assertLess(composed['body'].index('## 私の正直な感想'),composed['body'].index('## よくある質問'))
        self.assertNotIn('body',a.article_schema(True)['required'])

    def test_illustrated_draft_reserves_four_diagrams_and_reuses_plan(self):
        data=response();article=json.loads(data['output'][1]['content'][0]['text'])
        markers=[f'【ここに図解{x}を挿入｜確認用】' for x in '①②③④']
        article['body']=article['body'].replace('## 私の正直な感想','\n'.join(markers)+'\n## 私の正直な感想')
        article['body']=article['body'].replace('## まとめ','## よくある質問\n確認用FAQ\n## まとめ').replace('## 参考リンク','## この記事について\n架空のテストです。\n## 参考リンク')
        article['images']=[{'number':i,'anchor':markers[i-1] if i else '', 'theme':'確認用','labels':['確認用'],'prompt':'one standalone image'} for i in range(5)]
        article.update(hashtags=['#確認'+str(i) for i in range(10)],threads='確認用の文章です。どう思いますか？')
        data['output'][1]['content'][0]['text']=json.dumps(article)
        config={**CONFIG,'images':{'enabled':True,'first_diagram':244}}
        with tempfile.TemporaryDirectory() as directory,patch.object(a,'make_request',return_value={}),patch.object(a,'call_api',return_value=data) as api, contextlib.redirect_stdout(io.StringIO()):
            output=Path(directory)
            a.run('morning',output,config,NOW,run_id='test')
            a.run('morning',output,config,NOW,run_id='test')
            self.assertEqual(api.call_count,1)
            state=json.loads((output/'state.json').read_text())
            self.assertEqual(state['next_diagram'],248)
            plan=json.loads((output/state['articles'][0]['media_plan']).read_text())
            self.assertEqual([x['filename'] for x in plan['items']],['thumbnail.png','diagram244.png','diagram245.png','diagram246.png','diagram247.png'])

    def test_three_independent_articles_and_retry_reuses_all(self):
        config={**CONFIG,'articles_per_slot':3}
        responses=[]
        for number in range(3):
            data=response()
            article=json.loads(data['output'][1]['content'][0]['text'])
            article['title']+=str(number)
            for source in article['sources']:
                old=source['url'];source['url']+=str(number)
                article['body']=article['body'].replace(old,source['url'])
            data['output'][0]['action']['sources']=article['sources']
            data['output'][1]['content'][0]['text']=json.dumps(article)
            responses.append(data)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)
            with patch.object(a,'call_api',side_effect=responses) as api, patch.object(a,'make_request',return_value={}), patch('publisher.publish') as publish, contextlib.redirect_stdout(io.StringIO()):
                ids=run_batch(output,config,NOW,NOW,'morning','draft',checkpoint=lambda:None)
                run_batch(output,config,NOW,NOW,'morning','draft',checkpoint=lambda:None)
                self.assertEqual(api.call_count,3)
                self.assertEqual(len(set(ids)),3)
                self.assertTrue(all(call.kwargs['dry_run'] for call in publish.call_args_list))
            state=json.loads((output/'state.json').read_text())
            self.assertEqual(state['next_number'],4)
            self.assertEqual(len(state['articles']),3)
            self.assertEqual(len({tuple(x['sources']) for x in state['articles']}),3)

    def test_upgrade_preserves_published_legacy_slot(self):
        ids=batch_run_ids({'articles_per_slot':3},{'articles':[{'run_id':'2026-10-10-morning'}]},'2026-10-10','morning')
        self.assertEqual(ids,['2026-10-10-morning','2026-10-10-morning-2','2026-10-10-morning-3'])

    def test_quota_error_stops_remaining_billed_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch('run_workflow.run',side_effect=a.APIError('残高不足')) as run:
            with self.assertRaises(a.APIError):
                run_batch(Path(directory),{**CONFIG,'articles_per_slot':3},NOW,NOW,'morning','draft',checkpoint=lambda:None)
            self.assertEqual(run.call_count,1)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.output=Path(self.temp.name)
        self.record={'number':1,'run_id':'test','title':'確認用の記事','path':'article.md','media_plan':'media/0001/plan.json'}
        (self.output/'article.md').write_text('本文')
        self.record['sha256']=hashlib.sha256('本文'.encode()).hexdigest()
        self.plan={'items':[{'number':i,'filename':f'{i}.png','status':'planned','attempts':0,'theme':'確認用','labels':['確認用'],'prompt':'one image','anchor':f'【ここに図解{"①②③④"[i-1]}を挿入｜確認用】' if i else ''} for i in range(5)]}
        a.atomic_json(self.output/'state.json',{'articles':[self.record]})
        a.atomic_json(self.output/self.record['media_plan'],self.plan)
        self.config={'images':{'size':'1536x864','model':'test','quality':'medium','max_attempts':2}}
        # Header-only bytes exercise validation/persistence, not image rendering.
        self.png=b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',1536,864)

    def tearDown(self):self.temp.cleanup()

    def test_ordered_five_images_and_completed_retry_never_rebills(self):
        import base64
        observed=[]
        def generate(payload):
            plan=json.loads((self.output/self.record['media_plan']).read_text())
            observed.append(next(x['number'] for x in plan['items'] if x['status']=='generating'))
            return {'data':[{'b64_json':base64.b64encode(self.png).decode()}]}
        approved={'approved':True,'issues':'','observed_title':'確認用'}
        with patch.object(media,'call_image_api',side_effect=generate) as call, patch.object(media,'review_image',return_value=approved), contextlib.redirect_stdout(io.StringIO()):
            media.ensure_images(self.output,self.config,'test')
            media.ensure_images(self.output,self.config,'test')
            self.assertEqual(call.call_count,5)
            self.assertEqual(observed,list(range(5)))
        self.assertIsNotNone(media.verified_media(self.output,self.record))

    def test_unknown_billed_outcome_blocks_regeneration(self):
        with patch.object(media,'call_image_api',side_effect=a.APIError('通信断')) as call:
            with self.assertRaises(a.APIError):media.ensure_images(self.output,self.config,'test')
            with self.assertRaisesRegex(a.WorkflowError,'結果が未確認'):
                media.ensure_images(self.output,self.config,'test')
            self.assertEqual(call.call_count,1)

    def test_rejected_image_alone_is_regenerated(self):
        import base64
        decisions=[{'approved':False,'issues':'文字誤り'}]+[{'approved':True,'issues':''}]*5
        with patch.object(media,'call_image_api',return_value={'data':[{'b64_json':base64.b64encode(self.png).decode()}]}) as call, patch.object(media,'review_image',side_effect=decisions), contextlib.redirect_stdout(io.StringIO()):
            media.ensure_images(self.output,self.config,'test')
            self.assertEqual(call.call_count,6)
        saved=json.loads((self.output/self.record['media_plan']).read_text())
        self.assertEqual([x['attempts'] for x in saved['items']],[2,1,1,1,1])

    def test_publish_body_has_captions_without_placeholders_or_local_images(self):
        source='\n'.join(x['anchor']+'\n![図](local.png)' for x in self.plan['items'][1:])
        result=prepare_body(source,self.plan)
        self.assertNotIn('ここに',result);self.assertNotIn('local.png',result)
        self.assertEqual(result.count('図解'),4)
