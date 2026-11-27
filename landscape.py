"""Sideways cards (split cards, Rooms, the front of Battles) in the app's big views.

Scryfall stores those cards as a normal portrait picture with the card turned on its side. The server's /img route returns
the picture turned upright when asked with "?r=1" (see app.py), and answers with the plain picture for every other card,
so the pages never need to know which cards are sideways. This snippet makes a page's hover preview, zoom or lightbox
<img> ask that way, and gives it a landscape size once an upright-sideways card has loaded.
"""

LANDSCAPE_CSS = """
#hover-preview.land, #fa-hover.land { width: 340px; border-radius: 3.5% / 4.75%; }
#ins-zoom:has(img.land), #rs-zoom:has(img.land) { width: 430px; }
#ins-zoom img.land, #rs-zoom img.land { border-radius: 10px; }
#lightbox-img.land, #br-lb-img.land { border-radius: 3.5% / 4.75%; }
.br-lb-art:has(img.land) { width: min(560px, 72vw); }
.br-tilt img.land { aspect-ratio: 7 / 5; }
"""

# Makes one <img> request the upright version of any URL under /img/, and mark itself ".land" when what loaded is wider than tall.
LANDSCAPE_JS = r"""
(function () {
  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  const upright = (u) => (typeof u === 'string' && u.indexOf('/img/') !== -1 && u.indexOf('r=1') === -1) ? u + (u.indexOf('?') === -1 ? '?r=1' : '&r=1') : u;
  window.bwUpright = function (img) {
    if (!img || img.__bwUpright) return;
    img.__bwUpright = true;
    Object.defineProperty(img, 'src', { configurable: true, get() { return desc.get.call(this); }, set(v) { desc.set.call(this, upright(v)); } });
    img.addEventListener('load', () => img.classList.toggle('land', img.naturalWidth > img.naturalHeight));
  };
})();
"""
