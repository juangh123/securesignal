(function () {
  "use strict";
  const SS = window.SS = {};

  SS.easeOutCubic = t => 1 - Math.pow(1 - t, 3);

  SS.el = (tag, cls, parent, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    if (parent) parent.appendChild(e);
    return e;
  };

  SS.particles = function (canvas, opts) {
    opts = opts || {};
    const ctx = canvas.getContext("2d");
    const W = canvas.width = 1920;
    const H = canvas.height = 1080;
    const N = opts.count || 44;
    const linkDist = opts.linkDist || 150;
    const color = opts.color || "rgba(96,165,250,";
    const pts = [];
    for (let i = 0; i < N; i++) {
      pts.push({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .34, vy: (Math.random() - .5) * .34, r: Math.random() * 1.7 + .6 });
    }
    let raf;
    const frame = () => {
      ctx.clearRect(0, 0, W, H);
      for (const p of pts) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > W) p.vx *= -1;
        if (p.y < 0 || p.y > H) p.vy *= -1;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = color + "0.55)"; ctx.fill();
      }
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        const a = pts[i], b = pts[j], dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy);
        if (d < linkDist) {
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = color + String((1 - d / linkDist) * 0.16) + ")";
          ctx.lineWidth = 1; ctx.stroke();
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return { stop() { cancelAnimationFrame(raf); } };
  };

  SS.radar = function (canvas, opts) {
    opts = opts || {};
    const ctx = canvas.getContext("2d");
    const W = canvas.width = canvas.clientWidth || 560;
    const H = canvas.height = canvas.clientHeight || 560;
    const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 8;
    const color = opts.color || "#34d399";
    const blips = [[.3, .1], [.72, .4], [.5, .72], [.88, .85]];
    let ang = 0, raf;
    const frame = () => {
      ctx.clearRect(0, 0, W, H);
      for (let i = 1; i <= 4; i++) {
        ctx.beginPath(); ctx.arc(cx, cy, R * i / 4, 0, Math.PI * 2);
        ctx.strokeStyle = color + "30"; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
      ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R);
      ctx.strokeStyle = color + "22"; ctx.lineWidth = 2; ctx.stroke();
      const g = ctx.createLinearGradient(0, 0, R, 0);
      g.addColorStop(0, "rgba(52,211,153,0)");
      g.addColorStop(.82, "rgba(52,211,153,.08)");
      g.addColorStop(1, "rgba(52,211,153,.5)");
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(ang);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R, 0, Math.PI * .62); ctx.closePath(); ctx.fill();
      ctx.restore();
      for (const rb of blips) {
        const bx = cx + Math.cos(rb[1] * Math.PI * 2) * R * rb[0];
        const by = cy + Math.sin(rb[1] * Math.PI * 2) * R * rb[0];
        ctx.beginPath(); ctx.arc(bx, by, 5, 0, Math.PI * 2);
        ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 14; ctx.fill(); ctx.shadowBlur = 0;
      }
      ang += .012;
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return { stop() { cancelAnimationFrame(raf); } };
  };

  SS.counter = function (el, to, opts) {
    opts = opts || {};
    const dur = opts.dur || 1.6, from = opts.from || 0;
    const dec = opts.decimals != null ? opts.decimals : 0;
    const pre = opts.prefix || "", suf = opts.suffix || "";
    const start = performance.now();
    const tick = now => {
      const t = Math.min(1, (now - start) / 1000 / dur);
      const v = from + (to - from) * SS.easeOutCubic(t);
      el.textContent = pre + v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suf;
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  SS.drawCheck = function (svgEl, opts) {
    opts = opts || {};
    const path = svgEl.querySelector(".check-path");
    if (!path) return;
    const len = path.getTotalLength();
    path.style.strokeDasharray = len;
    path.style.strokeDashoffset = len;
    path.style.transition = "stroke-dashoffset " + (opts.dur || .6) + "s ease-out " + (opts.delay || 0) + "s";
    requestAnimationFrame(() => requestAnimationFrame(() => { path.style.strokeDashoffset = 0; }));
  };

  SS.scanlines = function (alpha) {
    const d = SS.el("div", "", document.body);
    d.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9999;opacity:" + (alpha != null ? alpha : .05) + ";background:repeating-linear-gradient(0deg,rgba(255,255,255,.5) 0 1px,transparent 1px 4px);mix-blend-mode:overlay;";
    return d;
  };

  SS.vignette = function () {
    const d = SS.el("div", "", document.body);
    d.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9998;background:radial-gradient(ellipse at center,transparent 58%,rgba(0,0,0,.45) 100%);";
    return d;
  };

  SS.grid = function (alpha, size) {
    const d = SS.el("div", "", document.body);
    d.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:0;opacity:" + (alpha != null ? alpha : .5) + ";background-image:linear-gradient(rgba(110,140,220,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(110,140,220,.09) 1px,transparent 1px);background-size:" + (size || 56) + "px " + (size || 56) + "px;";
    return d;
  };
})();
