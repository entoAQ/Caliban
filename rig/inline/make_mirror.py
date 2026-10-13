"""Build sgsc-app/destoner-mirror.html from rig/inline/screen.html: same look
and render code, data from Supabase (inline_rig_status, posted by the rig
through Caliban's /inline/state), read-only.

Run after any change to screen.html, then commit the result in sgsc-app:

    git show HEAD:rig/inline/screen.html > /tmp/screen.html
    python rig/inline/make_mirror.py /tmp/screen.html ../sgsc-app/destoner-mirror.html

From the committed screen.html, not the working copy, so a half-finished
change never reaches SGSC. Every replacement asserts it matched exactly once:
if screen.html has moved on so a piece no longer matches, this stops rather
than producing a mirror with that piece silently missing.
"""
import sys

src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep('<title>Destoner</title>', '''<title>Destoner (miroir)</title>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>''')

rep('''<!--
  The destoner screen:''', '''<!--
  MIRROR OF THE DESTONER SCREEN -- generated, do not edit by hand.

  Built from rig/inline/screen.html in the Caliban repo (the screen the Pi
  `Ariel` shows beside the destoner), with only the data source changed: the
  state comes from Supabase (inline_rig_status, which the rig posts through
  Caliban's /inline/state at least once a minute) instead of the Pi's
  localhost, the photo from Caliban's stored review copy, and nothing can be
  confirmed here -- confirmations belong to the person at the destoner.
  When screen.html changes, regenerate this file rather than editing it.

  Original header:
  The destoner screen:''')

# A browser window, not a 720x1280 kiosk: keep the screen's proportions, centred.
rep('''  body { display: flex; flex-direction: column; padding: 16px; gap: 14px; }''',
    '''  body { display: flex; flex-direction: column; padding: 16px; gap: 14px;
    max-width: 720px; margin: 0 auto; box-sizing: border-box; }
  html { overflow: auto; } body { overflow: visible; height: auto; min-height: 100%; }
  #mirror-bar { font-size: 14px; color: var(--muted); display: flex; justify-content: space-between; gap: 10px; }
  #mirror-bar.late { color: var(--bad); font-weight: 700; }
  .mirror-wait { background: var(--panel); border-radius: 12px; padding: 16px 20px; font-size: 18px;
    color: var(--muted); text-align: center; }''')

rep('''  <header>
    <h1>Destoner</h1>''', '''  <div id="mirror-bar"><span>Miroir de l'écran du destoner — lecture seule</span><span id="mirror-age"></span></div>
  <header>
    <h1>Destoner</h1>''')

rep('''const POLL_MS = 2000;''', '''const POLL_MS = 10000;
const MIRROR = true;
// The rig posts at least once a minute; older than this, it has stopped.
const MIRROR_LATE_MIN = 3;
const SUPABASE_URL = 'https://pzjsxzgojucqaippctjy.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InB6anN4emdvanVjcWFpcHBjdGp5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA3MzAwNTgsImV4cCI6MjA5NjMwNjA1OH0.YVY2AcAOs8CNK5nBmya155KTJSvukQ7KjgQ_tTe3UP4';
const PHOTO_BUCKET = 'vision-band-test-captures';
const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
let mirrorUpdated = null;''')

rep('''async function poll() {
  try {
    const r = await fetch('/state', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    failures = 0;
    // screen.py serves a new screen.html after an update; pick it up.
    if (version && s.server && s.server.version !== version) { location.reload(); return; }
    version = s.server && s.server.version;
    state = s;
    render();
  } catch (e) {
    failures++;
    if (failures >= 3) overlay('Écran en redémarrage', 'Le service local ne répond pas. Il redémarre seul — patientez.');
  }
}''', '''async function poll() {
  try {
    const { data, error } = await sb.from('inline_rig_status')
      .select('state, updated_at').eq('device', 'ariel').maybeSingle();
    if (error) throw new Error(error.message);
    failures = 0;
    if (!data) { overlay('Aucune donnée du banc', "Le banc n'a encore rien envoyé."); return; }
    mirrorUpdated = data.updated_at;
    state = data.state || {};
    render();
    mirrorAge();
  } catch (e) {
    failures++;
    if (failures >= 2) overlay('Miroir indisponible', String(e.message || e));
  }
}

function mirrorAge() {
  if (!mirrorUpdated) return;
  const m = minsAgo(mirrorUpdated);
  const late = m > MIRROR_LATE_MIN;
  $('mirror-bar').className = late ? 'late' : '';
  $('mirror-age').textContent = late
    ? `⚠ Banc silencieux depuis ${Math.floor(m)} min`
    : `mis à jour ${agoText(mirrorUpdated)}`;
}

// Same session and role as the rest of SGSC: QC and up read the table.
async function gate() {
  const { data } = await sb.auth.getSession();
  if (!data.session) {
    overlay('Connexion requise', "Connectez-vous d'abord à SGSC, puis rouvrez cette page.");
    return false;
  }
  return true;
}''')

# Photos: Caliban's review copy of each reading, at inline/<reading id>.jpg.
rep('''    img.src = '/photo?id=' + encodeURIComponent(r.id);''',
    '''    sb.storage.from(PHOTO_BUCKET).createSignedUrl(`inline/${r.id}.jpg`, 600)
      .then(({ data }) => { if (data) img.src = data.signedUrl; });''')

# Read-only: show what was confirmed, never buttons.
rep('''  $('done').hidden = true;
''', '''  $('done').hidden = true;
  if (MIRROR) {
    box.dataset.sig = '';
    box.innerHTML = '<div class="mirror-wait">En attente de confirmation sur l\\'écran du destoner</div>';
    return;
  }
''')

# Start only once signed in.
rep('''setInterval(poll, POLL_MS);
poll();''', '''gate().then(ok => {
  if (!ok) return;
  setInterval(poll, POLL_MS);
  setInterval(mirrorAge, 15000);
  poll();
});''')

open(out, "w", encoding="utf-8", newline="\n").write(s)
print("written", out)
