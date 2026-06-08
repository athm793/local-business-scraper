"""
Anti-bot detection layer.

Layers applied:
  1. JS init script — removes navigator.webdriver, fakes plugins/chrome runtime/WebGL
  2. Human mouse movement — bezier curves with easing and micro-jitter
  3. Human scroll — stepped with easing and occasional overshoot
  4. Gaussian delays — realistic timing distribution, not uniform random
  5. Random idle pauses — occasional thinking/reading pauses
  6. CAPTCHA / block detection with cooldown + retry
  7. User-agent and viewport rotation per worker session
"""

import asyncio
import math
import random
import weakref

# ── Fingerprint pools ─────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 900},
]

BLOCK_SIGNALS = [
    "unusual traffic",
    "not a robot",
    "recaptcha",
    "i'm not a robot",
    "automated query",
    "access denied",
    "403 forbidden",
    "our systems have detected",
    "captcha",
    "verify you are human",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    vp = random.choice(VIEWPORTS)
    return {
        "width":  vp["width"]  + random.randint(-30, 30),
        "height": vp["height"] + random.randint(-20, 20),
    }


# ── JS stealth init script ────────────────────────────────────────────────────

_STEALTH_JS = """
// ── 1. Remove automation flag ─────────────────────────────────────────────────
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// ── 2. Realistic plugins ──────────────────────────────────────────────────────
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    function FakePlugin(name, filename, desc) {
      this.name = name; this.description = desc; this.filename = filename;
      this.length = 1;
    }
    const ps = [
      new FakePlugin('PDF Viewer',              'internal-pdf-viewer',              'Portable Document Format'),
      new FakePlugin('Chrome PDF Viewer',       'mhjfbmdgcfjbbpaeojofohoefgiehjai', 'Portable Document Format'),
      new FakePlugin('Chromium PDF Viewer',     'internal-pdf-viewer',              'Portable Document Format'),
      new FakePlugin('Microsoft Edge PDF Viewer','edge-pdf-viewer',                 'Portable Document Format'),
      new FakePlugin('WebKit built-in PDF',     'webkit-pdf-viewer',                'Portable Document Format'),
    ];
    ps.item      = (i) => ps[i];
    ps.namedItem = (n) => ps.find(p => p.name === n);
    ps.refresh   = () => {};
    return ps;
  }
});

// ── 3. Languages ──────────────────────────────────────────────────────────────
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// ── 4. Chrome runtime object ──────────────────────────────────────────────────
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) {
  window.chrome.runtime = {
    id: undefined,
    connect: () => {},
    sendMessage: () => {},
    onMessage: { addListener: () => {}, removeListener: () => {} },
    PlatformOs:   { MAC:'mac', WIN:'win', ANDROID:'android', LINUX:'linux' },
    PlatformArch: { ARM:'arm', X86_32:'x86-32', X86_64:'x86-64' }
  };
}
if (!window.chrome.app) {
  window.chrome.app = {
    isInstalled: false,
    getDetails: () => null,
    getIsInstalled: () => false,
    runningState: () => 'cannot_run'
  };
}

// ── 5. Permissions — don't reveal automation ──────────────────────────────────
(function() {
  const _orig = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (params) => {
    if (params.name === 'notifications') {
      return Promise.resolve({ state: 'prompt', onchange: null });
    }
    return _orig(params);
  };
})();

// ── 6. WebGL — realistic GPU strings ─────────────────────────────────────────
(function() {
  const patchGL = (Ctor) => {
    if (!Ctor) return;
    const orig = Ctor.prototype.getParameter;
    Ctor.prototype.getParameter = function(p) {
      if (p === 37445) return 'Intel Inc.';
      if (p === 37446) return 'Intel(R) UHD Graphics 630';
      return orig.call(this, p);
    };
  };
  patchGL(window.WebGLRenderingContext);
  patchGL(window.WebGL2RenderingContext);
})();

// ── 7. Consistent navigator.platform ─────────────────────────────────────────
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// ── 8. Hide document.hidden / focus tricks used in bot checks ─────────────────
Object.defineProperty(document, 'hidden',            { get: () => false });
Object.defineProperty(document, 'visibilityState',   { get: () => 'visible' });
document.addEventListener('visibilitychange', (e) => e.stopImmediatePropagation(), true);

// ── 9. Realistic connection object ────────────────────────────────────────────
if (navigator.connection) {
  Object.defineProperty(navigator.connection, 'rtt',            { get: () => 100 });
  Object.defineProperty(navigator.connection, 'downlink',       { get: () => 10  });
  Object.defineProperty(navigator.connection, 'effectiveType',  { get: () => '4g' });
  Object.defineProperty(navigator.connection, 'saveData',       { get: () => false });
}

// ── 10. Remove automation-only properties ─────────────────────────────────────
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
"""


async def apply_stealth(page):
    """Inject all stealth patches as an init script (runs before every page load)."""
    await page.add_init_script(_STEALTH_JS)


# ── Timing ────────────────────────────────────────────────────────────────────

async def delay(mean: float = 4.0, std: float = 1.5,
                min_s: float = 2.0, max_s: float = 12.0):
    """Gaussian delay — realistic timing, not uniform random."""
    t = random.gauss(mean, std)
    await asyncio.sleep(max(min_s, min(max_s, t)))


async def short_delay(mean: float = 0.8, std: float = 0.3):
    await asyncio.sleep(max(0.3, random.gauss(mean, std)))


async def maybe_idle():
    """5% chance of a longer 'human reading' pause (3–8s)."""
    if random.random() < 0.05:
        await asyncio.sleep(random.uniform(3.0, 8.0))


# ── Mouse movement ────────────────────────────────────────────────────────────

# Per-page mouse state — WeakKeyDictionary so entries are GC'd with the page object.
# Module-level globals would be shared across all workers, corrupting bezier origins.
_mouse_state: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _get_mouse(page) -> tuple[float, float]:
    return _mouse_state.get(page, (400.0, 300.0))


def _set_mouse(page, x: float, y: float) -> None:
    _mouse_state[page] = (x, y)


def _cubic_bezier(t: float, p0, cp1, cp2, p1) -> float:
    """Evaluate one axis of a cubic bezier at parameter t."""
    return (
        (1 - t) ** 3 * p0
        + 3 * (1 - t) ** 2 * t * cp1
        + 3 * (1 - t) * t ** 2 * cp2
        + t ** 3 * p1
    )


async def human_move(page, tx: float, ty: float):
    """Move mouse from current position to (tx, ty) along a bezier curve."""
    sx, sy = _get_mouse(page)

    dx, dy = tx - sx, ty - sy
    dist   = math.hypot(dx, dy) or 1

    # Control points — offset perpendicular to the path for a natural curve
    perp   = random.uniform(-0.25, 0.25) * dist
    angle  = math.atan2(dy, dx) + math.pi / 2
    mid_x  = (sx + tx) / 2 + math.cos(angle) * perp
    mid_y  = (sy + ty) / 2 + math.sin(angle) * perp

    cp1x = sx + (mid_x - sx) * random.uniform(0.3, 0.7)
    cp1y = sy + (mid_y - sy) * random.uniform(0.3, 0.7)
    cp2x = tx + (mid_x - tx) * random.uniform(0.3, 0.7)
    cp2y = ty + (mid_y - ty) * random.uniform(0.3, 0.7)

    steps = max(15, min(40, int(dist / 20)))

    for i in range(steps + 1):
        t = i / steps
        # Ease-in-out: slow at edges, fast in the middle
        t_eased = t * t * (3 - 2 * t)

        mx = _cubic_bezier(t_eased, sx, cp1x, cp2x, tx) + random.gauss(0, 0.4)
        my = _cubic_bezier(t_eased, sy, cp1y, cp2y, ty) + random.gauss(0, 0.4)

        await page.mouse.move(mx, my)

        # Speed: slow start/end, fast middle
        edge_dist = abs(2 * t - 1)          # 0 at midpoint, 1 at ends
        step_delay = 0.008 + edge_dist * 0.025
        await asyncio.sleep(step_delay)

    _set_mouse(page, tx, ty)


async def human_click(page, element=None, x: float = None, y: float = None):
    """Move to element/coords with bezier curve, dwell briefly, then click."""
    if element is not None:
        try:
            box = await element.bounding_box()
            if box:
                tx = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
                ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await human_move(page, tx, ty)
                await asyncio.sleep(random.uniform(0.05, 0.18))
                await page.mouse.click(tx, ty)
                return
        except Exception:
            pass
        await element.click()
    elif x is not None and y is not None:
        await human_move(page, x, y)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.mouse.click(x, y)


# ── Scrolling ─────────────────────────────────────────────────────────────────

async def human_scroll(page, element, pixels: int):
    """Scroll element with easing and an occasional overshoot-then-correct."""
    steps = random.randint(6, 12)
    scrolled = 0.0

    for i in range(steps):
        t = (i + 1) / steps
        t_eased = t * t * (3 - 2 * t)      # smooth-step
        target  = pixels * t_eased
        chunk   = target - scrolled
        await page.evaluate(f"el => el.scrollBy(0, {chunk:.1f})", element)
        scrolled = target
        await asyncio.sleep(random.gauss(0.12, 0.04))

    # ~25% chance of a small overshoot + correction
    if random.random() < 0.25:
        overshoot = random.randint(15, 50)
        await page.evaluate(f"el => el.scrollBy(0, {overshoot})", element)
        await asyncio.sleep(random.uniform(0.2, 0.5))
        await page.evaluate(f"el => el.scrollBy(0, -{overshoot})", element)


# ── CAPTCHA / block detection ─────────────────────────────────────────────────

async def is_blocked(page) -> bool:
    """Return True if Google has shown a CAPTCHA or block page."""
    try:
        content = (await page.content()).lower()
        return any(sig in content for sig in BLOCK_SIGNALS)
    except Exception:
        return False


async def handle_block(page, log_fn, location: str, stop_event=None) -> bool:
    """
    Called when a block is detected.
    Waits, navigates to Google homepage, then returns True if we should retry
    or False if still blocked after cooldown (or if stop was requested).
    """
    cooldown = random.uniform(80, 110)
    log_fn(f"[BLOCK] CAPTCHA/block on '{location}' — cooling down {cooldown:.0f}s...")
    elapsed = 0.0
    while elapsed < cooldown:
        if stop_event is not None and stop_event.is_set():
            log_fn("[BLOCK] Cooldown interrupted by stop request")
            return False
        chunk = min(5.0, cooldown - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk

    # Visit Google home to reset session state
    try:
        await page.goto("https://www.google.com",
                        wait_until="domcontentloaded", timeout=30000)
        await delay(4, 1.5, 2, 8)
    except Exception:
        pass

    if await is_blocked(page):
        log_fn(f"[BLOCK] Still blocked after cooldown — skipping '{location}'")
        return False

    log_fn(f"[BLOCK] Cooldown complete — retrying '{location}'")
    return True
