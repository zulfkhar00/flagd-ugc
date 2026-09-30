"""One bounded replacement pass after inspection; retain rejected images."""
import sys
from pathlib import Path
from run import main
root = Path(__file__).resolve().parent
with (root / 'replacement-pass-01.txt').open('x') as handle:
    handle.write('Replace hallway lighting and kitchen mirror framing; two images only.\n')
for key in ('hallway-skeptical', 'kitchen-suppressed-laugh'):
    folder = root / key
    (folder / 'scene.png').rename(folder / 'rejected-scene-01.png')
    (folder / 'image-submission.json').rename(folder / 'rejected-image-submission-01.json')
sys.argv = [sys.argv[0], 'scenes']
main()
