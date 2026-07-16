import os, glob, re
for f in glob.glob('c:/Users/PC/Downloads/robot-fleet-platform/frontend/robot-fleet-dashboard/src/components/*.jsx'):
    with open(f, 'r', encoding='utf8') as file:
        content = file.read()
    content = re.sub(r'className="glassStrong sheen( [^"]+)?"', r'className="panel\1"', content)
    content = re.sub(r'className="glass( [^"]+)?"', r'className="panel\1"', content)
    content = re.sub(r'className="glass"', r'className="panel"', content)
    with open(f, 'w', encoding='utf8') as file:
        file.write(content)
