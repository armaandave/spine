/* DOM chrome in Burst's language: intro/loader, mode index, hover label, corner copy.
   Inks follow the mode's theme. Everything eases; nothing pops. */
import gsap from "gsap";
import type { Mode, WorldData } from "../engine/types";

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text?: string, parent?: HTMLElement) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  parent?.append(e);
  return e;
};

export interface UIHooks { open(id: number): void; close(): void; switchTo(id: string): void }

export class UI {
  root: HTMLElement;
  private intro: HTMLElement; private introLabel: HTMLElement; private introCount: HTMLElement;
  private nav: HTMLElement; private navU: HTMLElement; private navBtns = new Map<string, HTMLButtonElement>();
  private tip: HTMLElement; private tipText: HTMLElement;
  private tipPos = { x: 0, y: 0, tx: 0, ty: 0, on: false, side: 1 };
  private progress = 0;

  constructor(root: HTMLElement, private world: WorldData | null, modes: Mode[], private hooks: UIHooks) {
    this.root = root;
    // intro: paper, a name and a mono count (no bar)
    this.intro = el("div", "intro", undefined, root);
    el("div", "intro-name", "Spine World", this.intro);
    this.introLabel = el("div", "intro-label", undefined, this.intro);
    this.introCount = el("b", "", "00", this.introLabel);
    this.introLabel.append(" / 100");

    // mode index
    this.nav = el("nav", "nav", undefined, root);
    modes.forEach((m, i) => {
      const b = el("button", "", undefined, this.nav) as HTMLButtonElement;
      b.dataset.mode = m.id;
      b.innerHTML = `<span>${String(i + 1).padStart(2, "0")}</span> ${m.name}`;
      b.onclick = () => hooks.switchTo(m.id);
      this.navBtns.set(m.id, b);
    });
    this.navU = el("i", "nav-u", undefined, this.nav);
    this.nav.style.opacity = "0";

    this.tip = el("div", "tip", undefined, root);
    this.tipText = el("span", "tip-text", undefined, this.tip);
    // native cursor only: pointer over a poster, grabbing while dragging
    addEventListener("pointerdown", () => { document.body.classList.add("grabbing"); });
    addEventListener("pointerup", () => { document.body.classList.remove("grabbing"); });
    addEventListener("resize", () => this.placeUnderline(false));
  }

  setWorld(w: WorldData) {
    this.world = w;
  }

  /* ---- intro ---- */
  setProgress(f: number) {
    this.progress = Math.max(this.progress, f);
    this.introCount.textContent = String(Math.round(this.progress * 100)).padStart(2, "0");
  }
  async finishIntro(full: boolean) {
    this.setProgress(1);
    gsap.to(this.intro, { opacity: 0, duration: full ? 0.6 : 0.35, ease: "power2.out", onComplete: () => this.intro.remove() });
    gsap.to(this.nav, { opacity: 1, duration: 0.6, delay: full ? 0.5 : 0.1 });
  }

  /* ---- nav ---- */
  setActive(id: string) {
    this.navBtns.forEach((b, k) => b.setAttribute("aria-current", String(k === id)));
    this.activeId = id;
    this.placeUnderline(true);
  }
  private activeId = "";
  private placeUnderline(animate: boolean) {
    const b = this.navBtns.get(this.activeId); if (!b) return;
    const w = b.offsetWidth, x = b.offsetLeft;
    gsap.to(this.navU, { x, width: w, duration: animate ? 0.5 : 0, ease: "expo.inOut", overwrite: true });
  }
  setTheme(theme: "dark" | "light") { document.body.dataset.theme = theme; }

  /* ---- hover label + cursor ---- */
  showTip(text: string, x: number, y: number, side = 1) {
    this.tipText.textContent = text;
    if (!this.tipPos.on) { this.tipPos.x = x; this.tipPos.y = y; }
    this.tipPos.tx = x; this.tipPos.ty = y; this.tipPos.on = true; this.tipPos.side = side;
    this.tip.classList.add("on");
    document.body.classList.add("on-poster");
  }
  hideTip() { if (!this.tipPos.on) return; this.tipPos.on = false; this.tip.classList.remove("on"); document.body.classList.remove("on-poster"); }
  tick(dt: number) {
    const k = 1 - Math.exp(-dt / 0.09);
    this.tipPos.x += (this.tipPos.tx - this.tipPos.x) * k; this.tipPos.y += (this.tipPos.ty - this.tipPos.y) * k;
    this.tip.style.transform = `translate3d(${this.tipPos.x}px, ${this.tipPos.y}px, 0)`;
  }
}
