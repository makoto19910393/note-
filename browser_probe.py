"""Inspect public note UI only; no credentials are used or saved."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    output = Path("browser-check")
    output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for name, url in [("login", "https://note.com/login"),
                          ("editor", "https://note.com/notes/new")]:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.locator("body").wait_for(state="visible")
            # Wait for client-rendered forms, not an arbitrary delay.
            page.locator("input").first.wait_for(state="visible", timeout=30000)
            data = page.evaluate("""() => ({
                url: location.href,
                title: document.title,
                fields: [...document.querySelectorAll('input, textarea')].map(el => ({
                    tag: el.tagName, type: el.type, name: el.name, id: el.id,
                    placeholder: el.getAttribute('placeholder'),
                    ariaLabel: el.getAttribute('aria-label'),
                    labels: [...(el.labels || [])].map(label => label.innerText)
                })),
                buttons: [...document.querySelectorAll('button')].map(el => el.innerText)
            })""")
            (output / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2))
            page.screenshot(path=str(output / (name + ".png")), full_page=True)
            print(name, "public UI checked")
        browser.close()


if __name__ == "__main__":
    main()
