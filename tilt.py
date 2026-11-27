"""The "held in the hand" card effect for the lightboxes that show one card large (the compare report's and the builder's).

The card tilts in 3D toward the pointer, its shadow slides the other way and a glare sweeps across it; a foil gets a
rainbow band as well. Same look as the Browse cards lightbox. bwTiltArt(img) wraps the lightbox <img> once and returns
{setFoil(bool), reset()}; the page calls setFoil when it opens a card and reset when it closes.
"""

TILT_CSS = """
.bw-art { display: inline-block; position: relative; perspective: 1300px; }
.bw-tilt { position: relative; border-radius: 4.7%; transform-style: preserve-3d; transform: rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg)) scale(var(--sc, 1));
  box-shadow: var(--sh, 0 10px 40px rgba(0,0,0,0.7)); transition: transform 0.55s cubic-bezier(.2,.8,.2,1), box-shadow 0.55s cubic-bezier(.2,.8,.2,1); will-change: transform; }
.bw-tilt.live { transition: transform 0.07s linear, box-shadow 0.07s linear; }
.bw-tilt > img { box-shadow: none !important; }
.bw-glare { position: absolute; inset: 0; border-radius: 4.7%; overflow: hidden; pointer-events: none; opacity: var(--go, 0); transition: opacity 0.4s ease; }
.bw-glare::before { content: ""; position: absolute; inset: 0; background: radial-gradient(farthest-corner circle at var(--gx, 50%) var(--gy, 50%), rgba(255,255,255,0.5) 0%, rgba(255,255,255,0.14) 30%, transparent 60%); mix-blend-mode: soft-light; }
.bw-glare::after { content: ""; position: absolute; inset: 0; opacity: 0; mix-blend-mode: color-dodge;
  background: linear-gradient(115deg, transparent 18%, rgba(255,0,150,0.55) 32%, rgba(255,220,0,0.5) 42%, rgba(0,255,190,0.5) 52%, rgba(0,150,255,0.55) 62%, transparent 78%);
  background-size: 280% 280%; background-position: var(--gx, 50%) var(--gy, 50%); }
.bw-tilt.holo .bw-glare::after { opacity: 0.55; }
@media (prefers-reduced-motion: reduce) { .bw-tilt, .bw-tilt.live { transition: none; } }
"""

TILT_JS = r"""
(function () {
  const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.bwTiltArt = function (img) {
    if (!img || !img.parentNode) return { setFoil() {}, reset() {} };
    const art = document.createElement('div'), tilt = document.createElement('div'), glare = document.createElement('div');
    art.className = 'bw-art'; tilt.className = 'bw-tilt'; glare.className = 'bw-glare';
    img.parentNode.insertBefore(art, img); art.appendChild(tilt); tilt.appendChild(img); tilt.appendChild(glare);
    const reset = () => { tilt.classList.remove('live'); ['--rx', '--ry', '--sc', '--sh', '--go'].forEach((k) => tilt.style.removeProperty(k)); };
    art.addEventListener('pointermove', (e) => {
      if (still) return;
      const r = art.getBoundingClientRect();
      const nx = Math.max(-1, Math.min(1, (e.clientX - r.left) / r.width * 2 - 1)), ny = Math.max(-1, Math.min(1, (e.clientY - r.top) / r.height * 2 - 1));
      const st = tilt.style;
      tilt.classList.add('live');
      st.setProperty('--ry', (nx * 14).toFixed(2) + 'deg'); st.setProperty('--rx', (-ny * 14).toFixed(2) + 'deg'); st.setProperty('--sc', '1.02');
      st.setProperty('--sh', (-nx * 22).toFixed(1) + 'px ' + (-ny * 22 + 14).toFixed(1) + 'px 38px rgba(0,0,0,0.65)');
      st.setProperty('--gx', ((nx + 1) * 50).toFixed(1) + '%'); st.setProperty('--gy', ((ny + 1) * 50).toFixed(1) + '%'); st.setProperty('--go', '1');
    });
    art.addEventListener('pointerleave', reset);
    return { setFoil(on) { tilt.classList.toggle('holo', !!on); }, reset };
  };
})();
"""
