"""Read the owner's published articles; export only an encrypted research archive.

No article bodies or login values are printed or committed. The ephemeral private
decryption key stays on the researcher's machine, outside this public repository.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile

from playwright.sync_api import sync_playwright


def main():
    output = Path(os.environ.get("RESEARCH_OUTPUT", "/tmp/private-note-corpus"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        public_key = work / "public.pem"
        public_key.write_text(os.environ["RESEARCH_PUBLIC_KEY"])
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=json.loads(os.environ["NOTE_STORAGE_STATE"]))
            try:
                articles = []
                for page in range(1, 100):
                    response = context.request.get(
                        "https://note.com/api/v2/creators/quiet_spirea2390/contents",
                        params={"kind": "note", "page": page}, timeout=45000)
                    if not response.ok:
                        raise RuntimeError("記事一覧を取得できません。")
                    data = response.json()["data"]
                    for item in data["contents"]:
                        detail = context.request.get(
                            "https://note.com/api/v3/notes/" + item["key"], timeout=45000)
                        if not detail.ok:
                            raise RuntimeError("公開記事の本文を取得できません。")
                        note = detail.json()["data"]
                        # Reading with the owner's existing session must unlock paid text.
                        if not note.get("is_my_note") or not note.get("can_read"):
                            raise RuntimeError("本人として記事本文を読めることを確認できません。")
                        articles.append({key: note.get(key) for key in (
                            "key", "name", "body", "price", "publish_at", "note_url",
                            "eyecatch", "pictures", "separator", "can_read", "is_my_note")})
                    if data["isLastPage"]:
                        break
                else:
                    raise RuntimeError("記事一覧の最終ページを確認できません。")
            finally:
                context.close()
                browser.close()
        payload = work / "corpus.json"
        payload.write_text(json.dumps(articles, ensure_ascii=False))
        payload.chmod(0o600)
        secret = os.urandom(32).hex().encode()
        password = work / "password"
        password.write_bytes(secret)
        password.chmod(0o600)
        subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
                        "-pass", "file:" + str(password), "-in", str(payload),
                        "-out", str(output / "corpus.enc")], check=True, capture_output=True)
        subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(public_key),
                        "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
                        "-in", str(password), "-out", str(output / "key.enc")],
                       check=True, capture_output=True)
        (output / "integrity.json").write_text(json.dumps({
            "count": len(articles), "paid_count": sum(a["price"] > 0 for a in articles),
            "hmac_sha256": hmac.new(secret, (output / "corpus.enc").read_bytes(), hashlib.sha256).hexdigest()
        }))
        print("本人の公開記事を確認し、暗号化した調査用資料を保存しました。記事数:", len(articles))


if __name__ == "__main__":
    main()
