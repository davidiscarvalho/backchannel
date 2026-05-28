/* Backchannel landing page — key modal + curl copy buttons */

function copyCurl(btn) {
  var cmd = btn.getAttribute('data-curl').replace('{base}', location.origin);
  navigator.clipboard.writeText(cmd).then(function() {
    var orig = btn.textContent;
    btn.textContent = 'copied!';
    setTimeout(function() { btn.textContent = orig; }, 1500);
  });
}

function openKeyModal() {
  var m = document.getElementById('key-modal');
  m.style.display = 'flex';
  document.getElementById('agent-label-input').focus();
  document.getElementById('key-result').style.display = 'none';
}

function closeKeyModal() {
  document.getElementById('key-modal').style.display = 'none';
  document.getElementById('agent-label-input').value = '';
  document.getElementById('key-result').style.display = 'none';
}

function issueKey() {
  var label = document.getElementById('agent-label-input').value.trim();
  if (!label) { alert('Enter an agent_label first.'); return; }
  var btn = document.querySelector('#key-modal button');
  btn.disabled = true;
  btn.textContent = 'Issuing\u2026';
  var result = document.getElementById('key-result');
  result.style.display = 'none';
  fetch('/v1/keys', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({agent_label: label})
  })
  .then(function(r) { return r.json().then(function(d) { return {ok: r.ok, data: d}; }); })
  .then(function(r) {
    result.style.display = 'block';
    if (r.ok) {
      result.style.borderColor = '#2a7a2a';
      result.innerHTML = '<div style="color:#5cff80;margin-bottom:8px;">&#10003; Key issued</div>'
        + '<div style="color:#ff9955;margin-bottom:4px;font-weight:bold;">&#9888; Copy and store it — you will not see this key again.</div>'
        + '<div style="background:#111;padding:8px;border-radius:6px;color:#e8ffe8;font-size:0.78rem;margin-top:8px;display:flex;align-items:center;gap:8px;">'
        + '<span style="flex:1;word-break:break-all;" id="key-value">' + r.data.key + '</span>'
        + '<button onclick="navigator.clipboard.writeText(document.getElementById(\'key-value\').textContent).then(function(){this.textContent=\'copied!\';setTimeout(function(){document.querySelector(\'#key-result button\').textContent=\'copy\'},1500)}.bind(this))" style="padding:4px 10px;border-radius:6px;border:1px solid #444;background:transparent;color:#8bcf90;font-family:var(--font-mono);font-size:0.72rem;cursor:pointer;white-space:nowrap;">copy</button>'
        + '</div>'
        + '<div style="color:#666;font-size:0.75rem;margin-top:8px;">Permanent &amp; free &middot; rate limit: ' + r.data.rate_limit + ' / ' + r.data.rate_limit_window_seconds + 's</div>';
    } else {
      result.style.borderColor = '#7a2a2a';
      result.innerHTML = '<div style="color:#ff5c5c;">Error: ' + (r.data.message || JSON.stringify(r.data)) + '</div>';
    }
  })
  .catch(function(e) {
    result.style.display = 'block';
    result.style.borderColor = '#7a2a2a';
    result.innerHTML = '<div style="color:#ff5c5c;">Request failed: ' + e.message + '</div>';
  })
  .finally(function() {
    btn.disabled = false;
    btn.textContent = 'Issue Key';
  });
}

document.getElementById('key-modal').addEventListener('click', function(e) {
  if (e.target === this) closeKeyModal();
});
