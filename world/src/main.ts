import "./style.css";
import { createEngine } from "./engine/engine";
import type { WorldData } from "./engine/types";
import { UI } from "./ui/ui";
import burst from "./modes/burst";
import orbit from "./modes/orbit";
import tunnel from "./modes/tunnel";

const modes = [burst, orbit, tunnel];

async function boot() {
  const app = document.getElementById("app")!;
  const hash = location.hash.slice(1);
  const full = !hash; // bare URL = the full opening; a #mode link (captures) skips straight in
  let engine: Awaited<ReturnType<typeof createEngine>>;
  const ui = new UI(app, null, modes, {
    open: (id) => engine.open(id),
    close: () => engine.close(),
    switchTo: (id) => engine.switchTo(id),
  });
  ui.setProgress(0.05);
  const world: WorldData = await (await fetch("data/world.json")).json();
  ui.setWorld(world);
  ui.setProgress(0.15);
  engine = await createEngine(app, world, modes, ui);
  await engine.intro(full, hash || modes[0].id);
}
boot();
