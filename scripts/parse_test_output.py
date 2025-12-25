import sys

# Force UTF-8 output
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'ignore')

with open('test_output.txt', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'Path construction for 8de' in line:
        for j in range(i, min(i+10, len(lines))):
            try:
                print(lines[j], end='')
            except:
                print('[ENCODING ERROR]')
        break
