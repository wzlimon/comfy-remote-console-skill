// 手机控制台前端（最小可用模板）。与 server_template.py 的 API 对齐。
// 生产环境按你自己的字段扩展表单与任务展示。

const S = { project: "", selected: [] };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("need_login"); }
  return r;
}

function showLogin() { document.getElementById("login").style.display = "block"; document.getElementById("app").style.display = "none"; }

async function doLogin() {
  const pw = document.getElementById("pw").value;
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
  const j = await r.json();
  if (j.ok) { document.getElementById("login").style.display = "none"; document.getElementById("app").style.display = "block"; loadRefs(); loadTasks(); }
  else alert("密码不对");
}

async function loadRefs() {
  S.project = document.getElementById("project").value.trim();
  const url = "/api/refs" + (S.project ? "?project=" + encodeURIComponent(S.project) : "");
  let refs = [];
  try { const j = await (await api(url)).json(); refs = j.refs || []; } catch (e) { return; }
  const g = document.getElementById("gallery");
  g.innerHTML = "";
  S.selected = [];
  refs.forEach(it => {
    const d = document.createElement("div");
    d.style.position = "relative";
    const img = document.createElement("img");
    img.src = it.url; img.loading = "lazy";
    img.onclick = () => {
      const i = S.selected.indexOf(it.rel_path);
      if (i >= 0) { S.selected.splice(i, 1); img.classList.remove("sel"); }
      else { S.selected.push(it.rel_path); img.classList.add("sel"); }
    };
    const t = document.createElement("span"); t.className = "gtype"; t.textContent = it.type;
    d.appendChild(img); d.appendChild(t); g.appendChild(d);
  });
}

async function submitTask() {
  const fd = new FormData();
  fd.append("prompt", document.getElementById("prompt").value);
  fd.append("mode", document.getElementById("mode").value);
  fd.append("ratio", document.getElementById("ratio").value);
  fd.append("duration", document.getElementById("duration").value);
  if (S.project) fd.append("project", S.project);
  S.selected.forEach((rp, i) => fd.append("ref_" + i + "_name", rp));
  try {
    const r = await api("/api/submit", { method: "POST", body: fd });
    const j = await r.json();
    if (j.ok) { alert("已提交 #" + j.id); loadTasks(); }
    else alert("失败：" + (j.error || "未知"));
  } catch (e) {}
}

async function loadTasks() {
  let list = [];
  try { const j = await (await api("/api/tasks")).json(); list = j.tasks || []; } catch (e) { return; }
  const box = document.getElementById("tasks");
  box.innerHTML = "";
  list.slice(0, 20).forEach(t => {
    const d = document.createElement("div"); d.className = "task";
    let html = `<span class="tag">#${t.id}</span><span class="tag">${t.mode}</span><span class="tag">${t.status}</span>`;
    if (t.project) html += `<span class="tag">${t.project}</span>`;
    if (t.result_file) html += ` <a class="dl" href="/video/${t.result_file}" target="_blank">查看</a>`;
    d.innerHTML = html;
    box.appendChild(d);
  });
}

// 启动：检测是否需要登录
(async () => {
  try {
    const j = await (await fetch("/api/me")).json();
    if (j.need_password && !j.logged_in) showLogin();
    else { document.getElementById("app").style.display = "block"; loadRefs(); loadTasks(); }
  } catch (e) { document.getElementById("app").style.display = "block"; loadRefs(); loadTasks(); }
})();

document.getElementById("project").addEventListener("change", loadRefs);
