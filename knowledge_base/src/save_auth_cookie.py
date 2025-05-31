"""
save_auth_cookie.py

Open a visible browser window to manually pass Cloudflare,
then save the authenticated storage state to auth.json.
"""

import asyncio
from playwright.async_api import async_playwright

async def save_auth_cookie():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # visible browser
        context = await browser.new_context()
        page = await context.new_page()

        print("🔄 Opening Nestlé homepage for human verification...")
        await page.goto(
            "https://www.madewithnestle.ca",
            wait_until="domcontentloaded",
            timeout=120000
        )

        # Give you time to complete the challenge
        print("⏳ Waiting 30 seconds for manual verification...")
        await page.wait_for_timeout(30000)

        # Save the authenticated state
        await context.storage_state(path="auth.json")
        print("✅ Cookie/state saved to auth.json")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(save_auth_cookie())
