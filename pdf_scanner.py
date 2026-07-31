"""
CLI tool — discovers all PDFs on the system and writes them to pdfs_found.txt.
Uses the shared scanner module for the actual filesystem traversal.
"""

import os
import time
from scanner import find_pdfs


def scan_all() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, 'pdfs_found.txt')

    print('=' * 60)
    print('PDF Scanner — Discovering all PDFs on this system')
    print(f'Output file: {output_path}')
    print('=' * 60)

    total = 0
    samples: list[str] = []
    last_dir = ''
    last_report = 0.0

    start_time = time.perf_counter()

    with open(output_path, 'w', encoding='utf-8') as out:
        out.write('PDF Files Found — Full List\n')
        out.write('=' * 60 + '\n')

        prev_dir = ''
        for path in find_pdfs():
            total += 1
            d = os.path.dirname(path)
            if d != prev_dir:
                out.write(f'\n[{d}]\n')
                prev_dir = d
            out.write(f'  {os.path.basename(path)}\n')

            if len(samples) < 20:
                samples.append(path)

            now = time.perf_counter()
            if now - last_report >= 3:
                print(f'  [{total:,} PDFs] {path}', end='\r')
                last_report = now

        out.write(f'\n{"=" * 60}\n')
        out.write(f'Total PDF files found: {total:,}\n')

    print(' ' * 80, end='\r')
    elapsed = time.perf_counter() - start_time

    print(f'{"=" * 60}')
    print(f'TOTAL PDF FILES FOUND:  {total:,}')
    print(f'Time taken:             {elapsed:.1f} seconds')
    print(f'Output written to:      {output_path}')
    print(f'{"=" * 60}')

    if samples:
        print(f'\nSample files (first {min(20, len(samples))}):')
        for p in samples[:20]:
            print(f'  {p}')


if __name__ == '__main__':
    scan_all()
