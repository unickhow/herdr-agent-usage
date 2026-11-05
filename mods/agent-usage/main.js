import { View, div, image } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, appHome, theme } from "herdr";

// Agent usage panel: reads local files only (see collect.py). Nothing leaves this machine.

const REFRESH_MS = 60000;
const script = () => `${appHome()}/mods/agent-usage/collect.py`;

const num = (v) => {
  v = Number(v) || 0;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v));
};

const ago = (sec) => {
  if (!sec) return "never";
  const s = Math.max(0, Date.now() / 1000 - sec);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

const until = (sec) => {
  const s = Math.max(0, sec - Date.now() / 1000), m = Math.floor(s / 60);
  if (m >= 1440) return `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
  if (m >= 60) return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.max(m, 1)}m`;
};

const shortModel = (m) => m.replace(/^claude-/, "");

const PROVIDERS = [
  { key: "claude", name: "Claude Code", icon: "icons/claude.svg" },
  { key: "codex", name: "Codex", icon: "icons/codex.svg" },
  { key: "antigravity", name: "Antigravity", icon: "icons/antigravity.svg" },
];

export default class AgentUsage extends View {
  init(_props, cx) {
    this.data = null;
    this.error = null;
    this.loading = false;
    this.at = 0;
    this.load(cx);
    cx.timer.every(REFRESH_MS, (cx) => this.load(cx));
  }

  load(cx) {
    if (this.loading) return;
    this.loading = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec("python3", [script()]);
        if (r.code !== 0) throw new Error((r.stderr || "collector failed").trim().slice(0, 200));
        this.data = JSON.parse(r.stdout);
        this.error = null;
        this.at = Date.now();
      } catch (e) {
        this.error = String(e.message ?? e);
      } finally {
        this.loading = false;
        cx.notify();
      }
    });
  }

  tone(t, pct) {
    return pct >= 90 ? t.ansi1 : pct >= 70 ? t.ansi3 : t.accent;
  }

  pill(t, text, color) {
    return div().h(18).px(7).flex().items_center().rounded(9).border(1).border_color(color).text_color(color).text_size(10.5).flex_none().child(text);
  }

  limit(t, l) {
    const reset = l.resets_at && l.resets_at * 1000 < Date.now();
    const pct = reset ? 0 : Math.max(0, Math.min(100, Number(l.used) || 0));
    const color = this.tone(t, pct);
    const right = reset ? "window reset" : `${Math.round(pct)}%${l.resets_at ? ` · resets in ${until(l.resets_at)}` : ""}`;
    return v_flex().gap(4)
      .child(h_flex().justify_between().text_size(11.5)
        .child(div().text_color(t.muted).child(l.label))
        .child(div().text_color(reset ? t.dim : t.foreground).child(right)))
      .child(div().w_full().h(6).rounded(3).bg(t.border)
        .child(div().h(6).rounded(3).bg(color).w(`${pct}%`)));
  }

  limits(t, p) {
    if (!p.limits?.length) return null;
    const stale = p.limits_as_of && Date.now() / 1000 - p.limits_as_of > 120;
    return v_flex().gap(8)
      .children(p.limits.map((l) => this.limit(t, l)))
      .when(stale, (el) => el.child(div().text_size(10.5).text_color(t.dim).child(`Limits as of ${ago(p.limits_as_of)}`)));
  }

  stats(t, totals, rows) {
    const col = (text, color, w) => div().flex().justify_end().w(w).flex_none().text_color(color).child(text);
    const head = h_flex().text_size(10.5).text_color(t.dim).child(div().flex_1().child(""))
      .child(col("Today", t.dim, 62)).child(col("7 days", t.dim, 62)).child(col("30 days", t.dim, 62));
    return v_flex().gap(3)
      .child(head)
      .children(rows.map(([label, field]) =>
        h_flex().text_size(11.5)
          .child(div().flex_1().text_color(t.muted).child(label))
          .child(col(num(totals.today[field]), t.foreground, 62))
          .child(col(num(totals.week[field]), t.foreground, 62))
          .child(col(num(totals.month[field]), t.foreground, 62))));
  }

  card(t, meta, p) {
    const status = p.error ? ["Error", t.ansi1]
      : p.signed_in ? ["Signed in", t.ansi2]
      : ["Signed out", t.ansi3];
    const body = [];
    if (!p.error) {
      const limits = this.limits(t, p);
      if (limits) body.push(limits);
      if (p.totals) {
        const rows = meta.key === "claude"
          ? [["Output", "output"], ["Input", "input"], ["Cache read", "cache_read"], ["Messages", "messages"]]
          : [["Requests", "requests"], ["Input", "input"], ["Cached", "cached"], ["Output", "output"]];
        body.push(this.stats(t, p.totals, rows));
      }
      if (meta.key === "claude" && p.top_models?.length) {
        body.push(div().text_size(10.5).text_color(t.dim).child(`Models (7 days): ${p.top_models.map((m) => shortModel(m.model)).join(" · ")}`));
      }
      if (meta.key === "claude" && !p.limits?.length) {
        body.push(div().text_size(10.5).text_color(t.dim).child("Limit bars need a Claude Code status line that saves its input (see the README)."));
      }
      if (meta.key === "antigravity") {
        body.push(div().text_size(11.5).text_color(t.muted).child(`Conversations stored: ${p.conversations ?? "unknown"}`));
        body.push(div().text_size(10.5).text_color(t.dim).child("Signed in is based on a stored token and is not verified with Google. Token and quota usage live on Google's servers, so none is shown."));
      }
      body.push(div().text_size(10.5).text_color(t.dim).child(`Last active ${ago(p.last_active)}`));
    } else if (p?.error) {
      body.push(div().text_size(10.5).text_color(t.dim).child(p.error));
    }
    return v_flex().gap(10).p(12).rounded(8).bg(t.surface).border(1).border_color(t.border)
      .child(h_flex().gap(8).items_center()
        .child(image(meta.icon).size(16).flex_none())
        .child(div().flex_1().min_w(0).truncate().text_size(13).font_semibold().text_color(t.foreground).child(meta.name))
        .when(Boolean(p?.plan), (el) => el.child(this.pill(t, String(p.plan).replace(/^./, (c) => c.toUpperCase()), t.muted)))
        .child(this.pill(t, status[0], status[1])))
      .children(body);
  }

  render() {
    const t = theme();
    const refresh = div().id("usage-refresh").h(24).px(8).flex().items_center().rounded(6).text_size(11.5).flex_none()
      .text_color(this.loading ? t.dim : t.muted).hover((s) => s.bg(t.hover).text_color(t.foreground))
      .on_click((_e, cx) => this.load(cx)).child(this.loading ? "Refreshing…" : "Refresh");
    const installed = this.data ? PROVIDERS.filter((meta) => this.data[meta.key]?.installed) : [];
    const stamp = this.at ? `Updated ${new Date(this.at).toTimeString().slice(0, 5)}` : "Loading…";
    return v_flex().id("usage-root").size_full().bg(t.surface).overflow_y_scrollbar()
      .child(h_flex().h(42).px(14).flex_none().items_center().gap(8).border_b(1).border_color(t.border)
        .child(div().text_size(13).font_semibold().text_color(t.foreground).child("Agent usage"))
        .child(div().flex_1().text_size(11).text_color(t.dim).child(stamp))
        .child(refresh))
      .child(v_flex().gap(10).p(12)
        .when(Boolean(this.error), (el) => el.child(div().p(10).rounded(8).border(1).border_color(t.ansi1).text_size(11.5).text_color(t.ansi1).child(`Couldn't read local usage: ${this.error}`)))
        .children(installed.map((meta) => this.card(t, meta, this.data[meta.key])))
        .when(Boolean(this.data) && installed.length === 0, (el) => el.child(div().text_size(11.5).text_color(t.muted).child("No supported agents detected on this machine.")))
        .child(div().text_size(10.5).text_color(t.dim).child("Read from local files only. Nothing is sent anywhere.")));
  }
}
