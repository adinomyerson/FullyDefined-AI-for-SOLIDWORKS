// Reads results.json and draws one card per problem. No libraries; works as a plain static page.
(function () {
  "use strict";

  var LABELS = { pass: "Pass", partial: "Partial", fail: "Fail" };
  var list = document.getElementById("problems");
  var score = document.getElementById("score");

  function text(tag, cls, value) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = value;
    return el;
  }

  function number(v) {
    return Number(v).toLocaleString("en", { maximumFractionDigits: 2 });
  }

  function xyz(v) {
    return "X=" + number(v[0]) + ", Y=" + number(v[1]) + ", Z=" + number(v[2]);
  }

  function accuracy(p) {
    if (p.ours === null || p.ours === undefined || !p.official) return "";
    // the measured difference, with its sign (- = our part came out smaller than the official answer)
    var diff = p.difference_percent !== undefined && p.difference_percent !== null
      ? Number(p.difference_percent)
      : ((p.ours - p.official) / Math.abs(p.official)) * 100;
    var sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
    return sign + number(Math.abs(diff)) + "% from the official answer";
  }

  function card(p) {
    var li = document.createElement("li");
    li.className = "pp-card";
    li.dataset.status = p.status;

    if (p.picture) {
      var img = document.createElement("img");
      img.src = p.picture;
      img.alt = "The model the AI built for " + p.name;
      img.loading = "lazy";
      li.appendChild(img);
    } else {
      li.appendChild(text("div", "pp-none", p.status === "fail" ? "Not built" : "Picture to come"));
    }

    var body = document.createElement("div");
    body.className = "pp-body";
    var h = document.createElement("h2");
    var a = document.createElement("a");
    a.href = p.url;
    a.textContent = p.name;
    a.rel = "noopener";
    h.appendChild(a);
    body.appendChild(h);
    body.appendChild(text("p", "pp-status " + p.status, LABELS[p.status] || p.status));

    var lines;
    if (Array.isArray(p.official)) {
      // an assembly: its centre of mass, and how far ours is from it (% of the official distance from the origin)
      lines = "Centre of mass: official " + xyz(p.official) + " " + p.unit;
      if (Array.isArray(p.ours)) {
        lines += ", ours " + xyz(p.ours) + " " + p.unit + " (" + number(p.difference_percent) + "% off)";
      }
    } else {
      var q = p.quantity === "volume" ? "Volume" : "Mass";
      lines = q + ": official " + number(p.official) + " " + p.unit;
      if (p.ours !== null && p.ours !== undefined) {
        lines += ", ours " + number(p.ours) + " " + p.unit + " (" + accuracy(p) + ")";
      }
    }
    body.appendChild(text("p", "pp-numbers", lines));
    if (p.reason) body.appendChild(text("p", "pp-reason", p.reason));
    li.appendChild(body);
    return li;
  }

  function filter(kind) {
    Array.prototype.forEach.call(list.children, function (li) {
      li.hidden = kind !== "all" && li.dataset.status !== kind;
    });
    document.querySelectorAll(".pp-filters button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.filter === kind));
    });
  }

  document.querySelectorAll(".pp-filters button").forEach(function (b) {
    b.addEventListener("click", function () { filter(b.dataset.filter); });
  });

  fetch("results.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then(function (data) {
      var problems = data.problems || [];
      problems.forEach(function (p) { list.appendChild(card(p)); });
      var solved = problems.filter(function (p) { return p.status === "pass"; }).length;
      var partial = problems.filter(function (p) { return p.status === "partial"; }).length;
      var within = data.pass_within_percent !== undefined ? data.pass_within_percent : 0.5;
      score.textContent = solved + " of " + problems.length + " within " + within + "% of the official answer" +
        (partial ? "; " + partial + " more within 2%" : "");
      if (data.source && data.source.url) document.getElementById("source-link").href = data.source.url;
      if (data.pass_within_percent !== undefined) document.getElementById("tolerance").textContent = data.pass_within_percent;
      if (data.updated) document.getElementById("updated").textContent = "Results for release " + data.updated + ".";
    })
    .catch(function () {
      score.textContent = "The results could not be loaded.";
    });
})();
