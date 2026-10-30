/*
  assets/kudos.js
  ===============
  Adds an "add reminder to give author kudos" button to the bottom of a fic
  page. Reminders live in localStorage (same as the top 25 ranker), so they're
  local to whatever machine/browser you're on. The list is shown on kudos.html.

  Injected into every archive page by phase4_process.py;
  assets/add_kudos.py backfills it into existing files.
*/
(function () {
  var STORE_KEY = "ficKudos.v1";

  function load() {
    try {
      var data = JSON.parse(localStorage.getItem(STORE_KEY));
      return Array.isArray(data) ? data : [];
    } catch (e) { return []; }
  }
  function save(list) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function metaContent(name) {
    var m = document.querySelector('meta[name="' + name + '"]');
    return m ? m.getAttribute("content") : "";
  }

  // AO3 work link from the preface, e.g. http://archiveofourown.org/works/59315482
  function workUrl() {
    var m = document.body.textContent.match(/archiveofourown\.org\/works\/(\d+)/);
    return m ? "https://archiveofourown.org/works/" + m[1] : "";
  }

  function init() {
    // archive/Starmora/x.html -> "Starmora/x.html"; archive/x.html -> "x.html"
    var m = location.pathname.match(/\/archive\/(.+)$/);
    var file = m ? decodeURIComponent(m[1]) : location.pathname.split("/").pop();
    var url = workUrl();
    var id = url ? url.split("/").pop() : file;
    var fic = {
      id: id,
      title: document.title || file,
      author: metaContent("fic-author") || metaContent("author") || "unknown",
      url: url,
      file: file,
    };

    var wrap = document.createElement("div");
    wrap.className = "kudos-reminder";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "kudos-btn";
    wrap.appendChild(btn);
    document.body.appendChild(wrap);

    function isSaved() {
      return load().some(function (f) { return f.id === fic.id; });
    }
    function render() {
      var saved = isSaved();
      btn.textContent = saved ? "♥ kudos reminder saved (click to remove)" : "♡ add reminder to give author kudos";
      btn.classList.toggle("saved", saved);
    }

    btn.addEventListener("click", function () {
      var list = load().filter(function (f) { return f.id !== fic.id; });
      if (!isSaved()) {
        fic.added = new Date().toISOString();
        list.push(fic);
      }
      save(list);
      render();
    });

    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
