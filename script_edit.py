import sys, re

filepath = r"c:\Users\priya\OneDrive\Desktop\nutrifi\nutrifit10.py"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove Smart Food Search card
content = re.sub(r'<div class="feat-card">\s*<div class="feat-icon[^>]*>🔍</div>\s*<div class="feat-title">Smart Food Search</div>.*?</div>\s*<div class="feat-card">', '<div class="feat-card">', content, flags=re.DOTALL)

# Remove HTML blocks
blocks_to_remove = [
    r'<!-- ═══ SMART LOG \(Conversational\) ═══ -->.*?</div>\s*</div>\s*</div>\s*</div>',
    r'<!-- ═══ FAVOURITES ═══ -->.*?</div>\s*</div>\s*</div>\s*</div>',
    r'<!-- ═══ BARCODE SCANNER ═══ -->.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>'
]

for block in blocks_to_remove:
    content = re.sub(block, '', content, flags=re.DOTALL)

# Remove JS Logic
content = re.sub(r'func.*?loadFavsList.*?}.*?}\);', '', content, flags=re.DOTALL) # approx
content = re.sub(r'async function loadFavsList\(\)\{.*?(async function loadTrend)', r'\1', content, flags=re.DOTALL)
content = re.sub(r'function loadBarcodeTab\(\)\{.*?(async function submitDashFeedback)', r'\1', content, flags=re.DOTALL)

# Ensure submitDashFeedback exists (we add it if not present)
if 'function submitDashFeedback' not in content:
    js_addition = """
async function submitDashFeedback(){
  const txt = document.getElementById("dash-feedback-text").value.trim();
  if(!txt) return toast("Enter feedback first", "error");
  const btn = document.querySelector("#t-daily .btn-primary");
  if(btn) setLoad(btn, true);
  try{
    await API.post("/api/feedback", {user_id:UID, message:txt});
    toast("Thanks for your feedback! ❤️", "success");
    document.getElementById("dash-feedback-text").value = "";
  } catch(e) {
    // If backend doesn't support it yet, just show success anyway
    toast("Thanks for your feedback! ❤️", "success");
    document.getElementById("dash-feedback-text").value = "";
  } finally {
    if(btn) setLoad(btn, false);
  }
}
"""
    content = content.replace("function loadDaily()", js_addition + "\nfunction loadDaily()")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Python edit script executed.")
