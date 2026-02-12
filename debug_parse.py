#!/usr/bin/env python3
"""Debug parsing to find duplicate issue."""

import asyncio
import logging
from collections import Counter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from scraper_prod import ProductionScraper, SEARCH_URL, AJAX_HEADERS

async def debug_parse():
    scraper = ProductionScraper()
    await scraper.start_session()
    await scraper.authenticate()
    
    data = {
        'year': '5',
        'book': 'PLD',
        'code': '',
        'court': '',
        'searchType': 'caselaw',
        'judge': '',
        'lawyer': '',
        'party': '',
        'Row': 0,
    }
    async with scraper.session.post(SEARCH_URL, data=data, headers=AJAX_HEADERS) as resp:
        html = await resp.text()
    
    cases, total = scraper._parse_search_results(html)
    
    print(f"Total parsed: {len(cases)}")
    print(f"Server total: {total}")
    
    # Check for duplicates
    case_ids = [c.get('case_id') for c in cases]
    counts = Counter(case_ids)
    
    print("\nCase ID frequency:")
    for case_id, count in counts.most_common():
        if count > 1:
            print(f"  {case_id}: {count} times (DUPLICATE)")
        else:
            print(f"  {case_id}: {count}")
    
    print(f"\nUnique cases: {len(set(case_ids))}")
    print(f"Total cases: {len(case_ids)}")
    
    await scraper.close()

if __name__ == "__main__":
    asyncio.run(debug_parse())
