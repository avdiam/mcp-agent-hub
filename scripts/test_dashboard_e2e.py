import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def main():
    # Path to save screenshot
    screenshot_dir = Path("C:/Users/30697/.gemini/antigravity-cli/brain/b5a2ab72-5e1e-4db8-afac-4ffe0f20ca97")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / "dashboard_screenshot.png"
    
    print("Launching headless Chromium...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        url = "http://localhost:8000/"
        print(f"Navigating to {url}...")
        await page.goto(url)
        
        # Wait for the agents table to populate
        print("Waiting for dashboard to load and populate...")
        await page.wait_for_selector("#agent-count")
        
        # Give it a second to fetch and render
        await page.wait_for_timeout(2000)
        
        # Assert page title
        title = await page.title()
        print(f"Dashboard Title: '{title}'")
        assert "MCP Agent Hub" in title, f"Unexpected title: {title}"
        
        # Read the active agent count
        agent_count_text = await page.locator("#agent-count").text_content()
        print(f"Agent Count Text: {agent_count_text}")
        
        # Check if our agent is listed in the table
        page_content = await page.content()
        if "antigravity-cli" in page_content:
            print("SUCCESS: Found 'antigravity-cli' in the dashboard agents table.")
        else:
            print("WARNING: 'antigravity-cli' not found in the agents table HTML.")
        
        # Take a screenshot
        print(f"Saving screenshot to {screenshot_path}...")
        await page.screenshot(path=str(screenshot_path), full_page=True)
        print("Screenshot saved successfully!")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
