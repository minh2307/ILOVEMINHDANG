import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

import json


async def inject_cookie():
    profile_dir = "/media/nguyen-son-minh/p5/MinhDang/runtime/chrome_profiles/cdha_automation"
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    
    cookies = []
    
    # Check for Gemini JSON cookie
    gemini_path = Path("/media/nguyen-son-minh/p5/MinhDang/Cookie_Gemini.txt")
    if gemini_path.exists():
        try:
            gemini_cookies = json.loads(gemini_path.read_text())
            for c in gemini_cookies:
                # Playwright expects specific format, remove extra fields like hostOnly
                pw_cookie = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                }
                if "expirationDate" in c:
                    pw_cookie["expires"] = int(c["expirationDate"])
                same_site = c.get("sameSite", "")
                if same_site == "no_restriction":
                    pw_cookie["sameSite"] = "None"
                elif same_site in ["strict", "lax"]:
                    pw_cookie["sameSite"] = same_site.capitalize()
                cookies.append(pw_cookie)
            print(f"Loaded {len(gemini_cookies)} cookies for Gemini.")
        except Exception as e:
            print(f"Failed to parse Gemini cookies: {e}")
            
    # Check for CDHA JSON cookie
    cdha_path = Path("/media/nguyen-son-minh/p5/MinhDang/Cookie_CDHA.txt")
    if cdha_path.exists():
        try:
            cdha_cookies = json.loads(cdha_path.read_text())
            for c in cdha_cookies:
                pw_cookie = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                }
                if "expirationDate" in c:
                    pw_cookie["expires"] = int(c["expirationDate"])
                same_site = c.get("sameSite", "")
                if same_site == "no_restriction":
                    pw_cookie["sameSite"] = "None"
                elif same_site in ["strict", "lax"]:
                    pw_cookie["sameSite"] = same_site.capitalize()
                cookies.append(pw_cookie)
            print(f"Loaded {len(cdha_cookies)} cookies for CDHA.")
        except Exception as e:
            print(f"Failed to parse CDHA cookies: {e}")
            
    if not cookies:
        print("No new cookies to inject.")
        return

    print(f"Injecting {len(cookies)} cookies: {cookies[:2]}...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            executable_path="/usr/bin/google-chrome",
            headless=True
        )
        await context.add_cookies(cookies)
        print("Cookies injected successfully into Playwright profile!")
        await context.close()

if __name__ == "__main__":
    asyncio.run(inject_cookie())
