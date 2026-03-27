import re

with open(r'c:\Users\priya\OneDrive\Desktop\nutrifi\nutrifit10.py', 'r', encoding='utf-8') as f:
    t10 = f.read()

with open(r'c:\Users\priya\OneDrive\Desktop\nutrifi\nutrifit9.py', 'r', encoding='utf-8') as f:
    t9 = f.read()

print("initTilt in t10:", t10.find("initTilt"))
print("qs in t10:", t10.find("function qs"))
print("page in t10:", t10.find("def page"))

with open('debug.txt', 'w', encoding='utf-8') as f:
    idx_qs = t10.find("function qs")
    f.write(t10[idx_qs-500:idx_qs+100])
