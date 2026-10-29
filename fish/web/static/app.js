// Fish: the page. It shows the snapshot this player's app pushes over
// /ws and sends commands back. All rules are checked by the host; the
// page only offers moves that make sense.
"use strict";

const SUIT = { S: "♠", H: "♥", D: "♦", C: "♣" };
const TEAM = ["Team Gold", "Team Tide"];
const SHORT = {
  "low-S": "2–7♠", "low-H": "2–7♥", "low-D": "2–7♦", "low-C": "2–7♣", eights: "8s & J",
  "high-S": "9–A♠", "high-H": "9–A♥", "high-D": "9–A♦", "high-C": "9–A♣",
};
const FISH_PATH = "M3 12c4-6 11-7 17-3l4-3v12l-4-3c-6 4-13 3-17-3z";

let snap = null;
let ws = null;
let ui = freshUi();
let seenClaims = null;

function freshUi() {
  return { mode: "ask", target: null, card: null, set: null, assign: {}, turnKey: null };
}

// ------------------------------------------------------------- socket

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => { snap = JSON.parse(e.data); render(); };
  ws.onclose = () => {
    snap = null;
    document.getElementById("app").innerHTML =
      '<p class="connecting">Lost the connection to your Fish app. Reconnecting…</p>';
    setTimeout(connect, 1000);
  };
}

function send(cmd) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
}

// ------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function cardName(c) {
  if (c === "WJ") return "white joker";
  if (c === "BJ") return "black joker";
  return c.slice(0, -1) + SUIT[c.slice(-1)];
}

function cardHtml(c, extra = "", tag = "div", attrs = "") {
  if (c === "WJ" || c === "BJ") {
    const cls = c === "BJ" ? "black" : "";
    return `<${tag} class="card joker ${cls} ${extra}" ${attrs} aria-label="${cardName(c)}">
      <span><span class="s">★</span>${c === "BJ" ? "Black" : "White"}</span></${tag}>`;
  }
  const suit = c.slice(-1);
  const red = suit === "H" || suit === "D" ? "red" : "";
  return `<${tag} class="card ${red} ${extra}" ${attrs} aria-label="${cardName(c)}">
    <span>${esc(c.slice(0, -1))}<span class="s">${SUIT[suit]}</span></span></${tag}>`;
}

const teamOf = (seat) => seat % 2;

function seatNames() {
  return (snap.lobby?.seats || []).map((s) => (s ? s.name : null));
}

function seatPos(i, me, n) {
  const rel = (i - me + n) % n;
  const a = ((90 + (rel * 360) / n) * Math.PI) / 180;
  return `left:${50 + 43 * Math.cos(a)}%;top:${50 + 41 * Math.sin(a)}%`;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// -------------------------------------------------------------- render

function render() {
  const app = document.getElementById("app");
  if (!snap) return;
  const err = snap.error ? `<p class="error" role="alert">${esc(snap.error)}</p>` : "";
  let body;
  if (!snap.joined) body = home();
  else if (!snap.lobby) body = joining();
  else if (snap.game) body = game();
  else body = lobby();
  app.innerHTML = body.replace("<!--error-->", err);
}

function home() {
  const rooms = snap.rooms.map((r) => {
    const label = r.started ? "Game in progress" : `${r.seated} of ${r.size} seats taken`;
    return `<li><div><strong>${esc(r.host_name || "Someone")}'s room</strong>
      <p class="quiet-text">${r.size} players. ${label}.</p></div>
      <button data-cmd="join" data-host="${esc(r.host)}" ${r.started ? "disabled" : ""}>Join</button></li>`;
  }).join("");
  const peers = snap.peers.map((p) => `<span class="chip">${esc(p.name)}</span>`).join("");
  return `<div class="home">
    <header><h1>Fish</h1>
      <p class="quiet-text">You're ${esc(snap.me.name)}. Two teams race to collect the most of the nine sets.
      Everyone plays from their own laptop, with no internet needed.</p></header>
    <!--error-->
    <section><h2>Rooms nearby</h2>
      ${rooms ? `<ul class="list">${rooms}</ul>`
        : '<p class="quiet-text">No rooms nearby yet. Host one, or wait for a friend to.</p>'}</section>
    <section><h2>Host a room</h2>
      <p class="quiet-text">You hold the deck: your app deals and checks every move.</p>
      <div class="host-choice">
        <button class="go" data-cmd="host" data-size="6">Host a 6-player room</button>
        <button class="quiet" data-cmd="host" data-size="8">Host an 8-player room</button></div></section>
    <section><h2>Players nearby</h2>
      ${peers ? `<div class="peers">${peers}</div>`
        : '<p class="quiet-text">Nobody yet. Players running Fish on the same network show up here.</p>'}</section>
  </div>`;
}

function joining() {
  const room = snap.rooms.find((r) => r.host === snap.joined);
  return `<div class="home"><header><h1>Fish</h1></header><!--error-->
    <p>Joining ${esc(room?.host_name || "the room")}…</p>
    <div><button class="quiet" data-cmd="leave">Cancel</button></div></div>`;
}

function fishes(claimed, fresh) {
  return `<div class="fishes">${Object.keys(snap.sets).map((id) => {
    const won = id in claimed ? `won${claimed[id]}` : "";
    const title = `${snap.set_names[id]}${id in claimed ? `, won by ${TEAM[claimed[id]]}` : ""}`;
    return `<div class="fish ${won} ${fresh.has(id) ? "fresh" : ""}" title="${esc(title)}">
      <svg viewBox="0 0 26 24" aria-hidden="true"><path d="${FISH_PATH}"/></svg>
      <span>${SHORT[id]}</span></div>`;
  }).join("")}</div>`;
}

function lobby() {
  const L = snap.lobby;
  const me = snap.me.address;
  const isHost = snap.hosting;
  const mySeat = L.seats.findIndex((s) => s && s.address === me);
  const seated = L.seats.filter(Boolean).length;
  const seats = L.seats.map((s, i) => {
    const cls = `seat t${teamOf(i)} ${s ? "" : "empty"}`;
    let inner;
    if (s) {
      const bot = s.address.startsWith("bot:");
      inner = `<div class="disc"><span class="name">${esc(s.name)}${s.address === me ? " (you)" : ""}</span>
        <span class="count">${TEAM[teamOf(i)]}</span></div>
        ${isHost && bot ? `<button class="quiet" data-cmd="remove_bot" data-seat="${i}">Remove</button>` : ""}`;
    } else {
      inner = `<div class="disc"><span class="name">Empty seat</span>
        <span class="count">${TEAM[teamOf(i)]}</span></div>
        <button data-cmd="sit" data-seat="${i}">Sit here</button>
        ${isHost ? `<button class="quiet" data-cmd="add_bot" data-seat="${i}">Add computer player</button>` : ""}`;
    }
    return `<div class="${cls}" style="${seatPos(i, Math.max(mySeat, 0), L.size)}">${inner}</div>`;
  }).join("");
  const standing = L.members.filter((m) => !L.seats.some((s) => s && s.address === m.address));
  const hostName = L.members.find((m) => m.address === L.host)?.name || "Host";
  const full = seated === L.size;
  return `<div class="room-bar">
      <div><h2>${esc(hostName)}'s room</h2>
      <p class="quiet-text">${seated} of ${L.size} seats taken. Teammates sit in every other seat.</p></div>
      <div class="actions">
        ${mySeat >= 0 ? '<button class="quiet" data-cmd="stand">Stand up</button>' : ""}
        ${isHost ? `<button class="go" data-cmd="start" ${full ? "" : "disabled"}>Start game</button>
          <button class="quiet" data-cmd="close">Close room</button>`
          : '<button class="quiet" data-cmd="leave">Leave room</button>'}
      </div></div>
    <!--error-->
    <div class="table"><div class="felt"></div>${seats}
      <div class="pond">${fishes({}, new Set())}
        <p class="quiet-text">${full ? (isHost ? "Everyone's seated. Start when you're ready."
          : `Waiting for ${esc(hostName)} to start.`)
          : "Pick a seat. Each team gets the seats of its colour."}</p></div></div>
    ${standing.length ? `<p class="quiet-text">Still standing: ${standing.map((m) => esc(m.name)).join(", ")}</p>` : ""}`;
}

function game() {
  const v = snap.game;
  const names = seatNames();
  const me = v.you;
  const myTeam = v.team;
  const myTurn = v.turn === me && !v.over;
  const teamTurn = teamOf(v.turn) === myTeam && !v.over;

  // Reset half-made choices when the turn moves on.
  const turnKey = `${v.turn}/${v.must_pass}/${Object.keys(v.claimed).length}/${v.hand.length}`;
  if (ui.turnKey !== turnKey) {
    ui = freshUi();
    ui.mode = myTurn ? "ask" : "declare";
    ui.turnKey = turnKey;
  }

  const claimedNow = new Set(Object.keys(v.claimed));
  const fresh = seenClaims ? new Set([...claimedNow].filter((id) => !seenClaims.has(id))) : new Set();
  seenClaims = claimedNow;

  const asking = myTurn && !v.must_pass && ui.mode === "ask";
  const seats = names.map((name, i) => {
    const can = asking && teamOf(i) !== myTeam && v.counts[i] > 0;
    const cls = `seat t${teamOf(i)} ${v.turn === i && !v.over ? "turn" : ""} ${ui.target === i ? "target" : ""}`;
    const disc = `<div class="disc"><span class="name">${esc(name)}${i === me ? " (you)" : ""}</span>
      <span class="count">${plural(v.counts[i], "card")}</span></div>`;
    const inner = can
      ? `<button class="pick" data-cmd="target" data-seat="${i}" aria-label="Ask ${esc(name)}">${disc}</button>`
      : disc;
    return `<div class="${cls}" style="${seatPos(i, me, v.players)}">${inner}</div>`;
  }).join("");

  const [g, t] = v.score;
  const pond = `<div class="pond">${fishes(v.claimed, fresh)}
    <p class="score"><span class="g">${g}</span> <span class="quiet-text">to</span> <span class="t">${t}</span></p>
    <div class="news">${news(v, names)}</div></div>`;

  const isHost = snap.hosting;
  const bar = `<div class="room-bar">
    <div><h2>Fish</h2><p class="quiet-text">You're on ${TEAM[myTeam]}, with
      ${names.filter((_, i) => teamOf(i) === myTeam && i !== me).map(esc).join(" and ")}.</p></div>
    <div class="actions">${v.over
      ? `${isHost ? '<button class="go" data-cmd="start">Play again</button><button class="quiet" data-cmd="close">Close room</button>'
        : '<button class="quiet" data-cmd="leave">Leave room</button>'}`
      : ""}</div></div>`;

  return `${bar}<!--error-->
    <div class="table"><div class="felt"></div>${seats}${pond}</div>
    <section class="you t${myTeam}">
      <header><p class="prompt ${myTurn ? "mine" : ""}">${prompt(v, names, myTurn, teamTurn)}</p></header>
      ${v.over ? "" : hand(v)}
      ${v.over ? "" : actions(v, names, myTurn, teamTurn)}
    </section>`;
}

function news(v, names) {
  const lines = [];
  const d = v.last_declaration;
  if (d) {
    const how = d.correct ? "correctly" : "wrongly";
    lines.push(`${esc(names[d.declarer])} declared ${esc(snap.set_names[d.set_id])} ${how}.
      ${TEAM[d.team]} wins it.`);
  }
  for (const a of [...v.history].reverse()) {
    lines.push(`${esc(names[a.asker])} asked ${esc(names[a.target])} for the ${cardName(a.card)}:
      ${a.got ? "got it" : "no"}.`);
  }
  if (!lines.length) lines.push(`${esc(names[v.turn])} goes first.`);
  return lines.map((l) => `<p>${l}</p>`).join("");
}

function prompt(v, names, myTurn, teamTurn) {
  if (v.over) {
    const [g, t] = v.score;
    const won = v.winner === v.team;
    return `${TEAM[v.winner]} wins, ${Math.max(g, t)} sets to ${Math.min(g, t)}. ${won ? "Well played." : "Next time."}`;
  }
  if (myTurn && v.must_pass) return "You're out of cards. Pick a teammate to take the turn.";
  if (myTurn) return "Your turn. Ask an opponent for a card, or declare a set.";
  if (teamTurn) return `${esc(names[v.turn])}'s turn. You can declare a set for your team.`;
  return `${esc(names[v.turn])}'s turn.`;
}

function hand(v) {
  if (!v.hand.length) return '<p class="quiet-text">You have no cards left. You can still declare on your team\'s turn.</p>';
  const groups = {};
  for (const c of v.hand) {
    const id = Object.keys(snap.sets).find((s) => snap.sets[s].includes(c));
    (groups[id] ||= []).push(c);
  }
  return `<div class="hand">${Object.entries(groups).map(([id, cards]) =>
    `<div class="group"><span class="label">${esc(snap.set_names[id])}</span>
      <div class="cards">${cards.map((c) => cardHtml(c)).join("")}</div></div>`).join("")}</div>`;
}

function actions(v, names, myTurn, teamTurn) {
  const me = v.you;
  const mates = names.map((_, i) => i).filter((i) => teamOf(i) === v.team);
  if (myTurn && v.must_pass) {
    return `<div class="panel"><div class="row">${mates.filter((i) => i !== me && v.counts[i] > 0).map((i) =>
      `<button data-cmd="pass" data-seat="${i}">Hand the turn to ${esc(names[i])}</button>`).join("")}</div>
      ${declarePanel(v, names, mates)}</div>`;
  }
  if (!teamTurn) return "";
  const tabs = myTurn ? `<div class="row">
      <button class="${ui.mode === "ask" ? "" : "quiet"}" data-cmd="mode" data-mode="ask">Ask for a card</button>
      <button class="${ui.mode === "declare" ? "" : "quiet"}" data-cmd="mode" data-mode="declare">Declare a set</button>
    </div>` : "";
  const body = myTurn && ui.mode === "ask" ? askPanel(v, names) : declarePanel(v, names, mates);
  return `<div class="panel">${tabs}${body}</div>`;
}

function askPanel(v, names) {
  const held = new Set(v.hand);
  const mySets = Object.keys(snap.sets).filter((id) => snap.sets[id].some((c) => held.has(c)));
  const opponents = names.map((_, i) => i).filter((i) => teamOf(i) !== v.team && v.counts[i] > 0);
  const who = `<div class="row">${opponents.map((i) =>
    `<button class="${ui.target === i ? "" : "quiet"}" data-cmd="target" data-seat="${i}">${esc(names[i])}</button>`).join("")}</div>`;
  const picker = `<div class="picker">${mySets.map((id) => `<div class="group">
      <span class="label">${esc(snap.set_names[id])}</span>
      <div class="cards">${snap.sets[id].map((c) => cardHtml(c,
        `${held.has(c) ? "held" : ""} ${ui.card === c ? "chosen" : ""}`, "button",
        `data-cmd="card" data-card="${c}" title="${held.has(c) ? "You hold this one: asking for it is a bluff" : ""}"`)).join("")}</div>
    </div>`).join("")}</div>`;
  const ready = ui.target !== null && ui.card;
  const bluff = ui.card && held.has(ui.card);
  const label = ready ? `Ask ${esc(names[ui.target])} for the ${cardName(ui.card)}` : "Ask";
  return `<p class="hint">Who do you ask? Tap an opponent at the table or here.</p>${who}
    <p class="hint">Which card? You can ask only from sets you hold. Faded cards are your own; asking for one is a bluff.</p>
    ${picker}
    <div class="row"><button class="go" data-cmd="ask" ${ready ? "" : "disabled"}>${label}</button>
      ${bluff ? '<span class="hint">This is a bluff: the answer will be no and the turn passes.</span>' : ""}</div>`;
}

function declarePanel(v, names, mates) {
  const open = Object.keys(snap.sets).filter((id) => !(id in v.claimed));
  const choose = `<div class="row">${open.map((id) =>
    `<button class="${ui.set === id ? "" : "quiet"}" data-cmd="set" data-set="${id}">${esc(snap.set_names[id])}</button>`).join("")}</div>`;
  if (!ui.set) {
    return `<p class="hint">Declare when you think all six cards of a set are on your team.
      Name who holds each one. If any card is wrong, the other team wins the set.</p>${choose}`;
  }
  const slots = snap.sets[ui.set].map((c) => `<div class="slot">${cardHtml(c)}
      <div class="row">${mates.map((i) => `<button class="${ui.assign[c] === i ? "" : "quiet"}"
        data-cmd="assign" data-card="${c}" data-seat="${i}">${esc(i === v.you ? "Me" : names[i])}</button>`).join("")}</div></div>`).join("");
  const ready = snap.sets[ui.set].every((c) => c in ui.assign);
  return `${choose}<p class="hint">Who holds each card? Your own cards are filled in.</p>
    <div class="assign">${slots}</div>
    <div class="row"><button class="go" data-cmd="declare" ${ready ? "" : "disabled"}>Declare ${esc(snap.set_names[ui.set])}</button></div>`;
}

// -------------------------------------------------------------- events

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-cmd]");
  if (!el || el.disabled || !snap) return;
  const d = el.dataset;
  const seat = d.seat !== undefined ? Number(d.seat) : undefined;
  switch (d.cmd) {
    case "host": return send({ cmd: "host", size: Number(d.size) });
    case "join": return send({ cmd: "join", host: d.host });
    case "leave": return send({ cmd: "leave" });
    case "close": return send({ cmd: "close" });
    case "sit": return send({ cmd: "sit", seat });
    case "stand": return send({ cmd: "sit", seat: null });
    case "add_bot": return send({ cmd: "add_bot", seat });
    case "remove_bot": return send({ cmd: "remove_bot", seat });
    case "start": seenClaims = null; return send({ cmd: "start" });
    case "pass": return send({ cmd: "act", action: "pass", to: seat });
    case "mode": ui.mode = d.mode; break;
    case "target": ui.target = seat; break;
    case "card": ui.card = d.card; break;
    case "ask":
      send({ cmd: "act", action: "ask", target: ui.target, card: ui.card });
      ui.card = null;
      break;
    case "set": {
      ui.set = d.set;
      ui.assign = {};
      const held = new Set(snap.game.hand);
      for (const c of snap.sets[d.set]) if (held.has(c)) ui.assign[c] = snap.game.you;
      break;
    }
    case "assign": ui.assign[d.card] = seat; break;
    case "declare":
      send({ cmd: "act", action: "declare", assignment: ui.assign });
      ui.set = null;
      ui.assign = {};
      break;
    default: return;
  }
  render();
});

connect();
