// Fish: the page. It shows the snapshot this player's app pushes over
// /ws and sends commands back. All rules are checked by the host; the
// page only offers moves that make sense.
//
// The same page runs on a phone (window.FISH_PHONE), where it first asks
// for a name and joins the laptop it was opened from.
"use strict";

const PHONE = Boolean(window.FISH_PHONE);
const GUEST_KEY = "fish-guest";

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
// The last move this page showed, so each new one is animated once, with
// the cards it moved: the change in every seat's count, and cards new to you.
let seen = freshSeen();
// A host action waiting for a second tap: "restart", "cancel" or "close".
let confirming = null;
// The game this page last showed, to tell players when the host ends it.
let lastGame = null;
let notice = "";

function freshSeen() {
  return { moves: null, counts: null, hand: null, deltas: [], newCards: new Set() };
}

const CONFIRM = {
  restart: { ask: "Deal a new game?", yes: "Deal again",
    detail: "Everyone's cards go back in the deck and a fresh game is dealt to the same seats." },
  cancel: { ask: "End this game?", yes: "End game",
    detail: "Nobody wins. Everyone goes back to the room in their seats, and you can change seats before dealing again." },
  close: { ask: "Close the room?", yes: "Close room",
    detail: "Everyone is sent back to the start and the seats are cleared." },
};

function freshUi() {
  return { mode: "ask", target: null, card: null, set: null, assign: {}, turnKey: null };
}

// ------------------------------------------------------------- socket

function connect() {
  let url = `ws://${location.host}/ws`;
  if (PHONE) {
    const guest = savedGuest();
    if (!guest) return phoneJoinForm();
    url += `?id=${encodeURIComponent(guest.id)}&s=${encodeURIComponent(guest.secret)}`;
  }
  ws = new WebSocket(url);
  ws.onmessage = (e) => { snap = JSON.parse(e.data); render(); };
  ws.onclose = (e) => {
    snap = null;
    if (PHONE && e.code === 4403) {
      // The laptop no longer knows this phone: join again.
      forgetGuest();
      return phoneJoinForm();
    }
    const what = PHONE ? "the laptop running the game" : "your Fish app";
    document.getElementById("app").innerHTML =
      `<p class="connecting">Lost the connection to ${what}. Reconnecting…</p>`;
    setTimeout(connect, 1500);
  };
}

// ------------------------------------------------------------- phones

function savedGuest() {
  try {
    const g = JSON.parse(localStorage.getItem(GUEST_KEY));
    return g && g.id && g.secret ? g : null;
  } catch {
    return null;
  }
}

function forgetGuest() {
  try { localStorage.removeItem(GUEST_KEY); } catch { /* storage blocked */ }
}

function phoneJoinForm(error = "") {
  const key = new URLSearchParams(location.search).get("k");
  const app = document.getElementById("app");
  if (!key) {
    app.innerHTML = `<div class="home"><header class="hero">${LOGO}<h1>Fish</h1>
      <p class="lede">Scan the QR code on the laptop running the game to join it.</p></header></div>`;
    return;
  }
  app.innerHTML = `<form class="home join-form" id="join"><header class="hero">${LOGO}<h1>Fish</h1>
      <p class="lede">You're joining a game on this Wi-Fi. Your cards stay on your phone.</p></header>
    ${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}
    <label for="name">Your name at the table</label>
    <input id="name" name="name" maxlength="24" autocomplete="nickname" autocapitalize="words" required>
    <div><button class="go" type="submit">Join the game</button></div></form>`;
  const form = document.getElementById("join");
  form.name.focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = form.name.value.trim();
    if (!name) return;
    let res;
    try {
      res = await fetch("/join", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ k: key, name }),
      });
    } catch {
      return phoneJoinForm("Can't reach the laptop. Check you're on the same Wi-Fi.");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return phoneJoinForm(body.error || "The laptop turned that down.");
    try { localStorage.setItem(GUEST_KEY, JSON.stringify(body)); } catch { /* storage blocked */ }
    if (!savedGuest()) return phoneJoinForm("This browser won't store your seat. Turn off private browsing and try again.");
    connect();
  });
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

// Where seat i sits round the table, in percent, with seat `me` at the bottom.
function seatXY(i, me, n) {
  const rel = (i - me + n) % n;
  const a = ((90 + (rel * 360) / n) * Math.PI) / 180;
  return { x: 50 + 43 * Math.cos(a), y: 50 + 41 * Math.sin(a) };
}

function seatPos(i, me, n) {
  const { x, y } = seatXY(i, me, n);
  return `left:${x}%;top:${y}%`;
}

function initial(name) {
  return (String(name || "?").replace(/ \(bot\)$/, "").trim()[0] || "?").toUpperCase();
}

const isBotName = (name) => / \(bot\)$/.test(name || "");
const bare = (name) => String(name || "").replace(/ \(bot\)$/, "");

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// -------------------------------------------------------------- render

function render() {
  const app = document.getElementById("app");
  if (!snap) return;
  let err = snap.error ? `<p class="error" role="alert">${esc(snap.error)}</p>` : "";
  if (snap.host_away && snap.lobby) {
    const host = snap.lobby.members.find((m) => m.address === snap.lobby.host)?.name || "the host";
    err += `<p class="error" role="status">Lost touch with ${esc(host)}, who runs this game.
      Your seat and cards are kept; play picks up when they're back in range.</p>`;
  }
  if (snap.game) {
    notice = "";
  } else {
    if (lastGame && !lastGame.over && snap.lobby && !snap.hosting) {
      notice = "The host ended the game. You're back in the room, in your seat.";
    }
    seen = freshSeen();
    seenClaims = null;
    ui = freshUi();
  }
  if (!snap.joined) notice = "";
  lastGame = snap.game;
  if (notice) err += `<p class="notice" role="status">${esc(notice)}</p>`;
  let body;
  if (PHONE && !snap.joined) body = phoneHome();
  else if (!snap.joined) body = home();
  else if (!snap.lobby) body = joining();
  else if (snap.game) body = game();
  else body = lobby();
  app.innerHTML = body.replace("<!--error-->", err);
}

const LOGO = `<svg class="logo" viewBox="0 0 26 24" aria-hidden="true"><path d="${FISH_PATH}"/></svg>`;

function topBar(title, sub, actions = "") {
  return `<header class="top-bar">
    <div class="brand">${LOGO}<div><h1 class="title">${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ""}</div></div>
    ${actions ? `<div class="actions">${actions}</div>` : ""}</header>`;
}

// A host button that asks again before it throws a game away.
function hostButton(what, label) {
  return `<button class="quiet ${confirming === what ? "armed" : ""}" data-cmd="confirm" data-what="${what}">${label}</button>`;
}

function confirmBar() {
  if (!confirming || !snap.hosting) return "";
  const c = CONFIRM[confirming];
  return `<div class="confirm" role="alertdialog" aria-labelledby="confirm-ask">
    <div><strong id="confirm-ask">${c.ask}</strong><p>${c.detail}</p></div>
    <div class="row"><button class="danger" data-cmd="${confirming}">${c.yes}</button>
      <button class="quiet" data-cmd="unconfirm">Keep playing</button></div></div>`;
}

function hostName() {
  const L = snap.lobby;
  return L?.members.find((m) => m.address === L.host)?.name || "Host";
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
    <header class="hero">${LOGO}<h1>Fish</h1>
      <p class="lede">You're <b>${esc(snap.me.name)}</b>. Two teams race to collect the most of the nine sets.
      Everyone plays from their own phone or laptop, with no internet needed.</p></header>
    <!--error-->
    <section><h2>Rooms nearby</h2>
      ${rooms ? `<ul class="list">${rooms}</ul>`
        : '<p class="quiet-text">No rooms nearby yet. Host one, or wait for a friend to.</p>'}</section>
    <section><h2>Host a room</h2>
      <p class="quiet-text">You hold the deck: your app deals and checks every move.</p>
      <div class="host-choice">
        <button class="size-pick" data-cmd="host" data-size="6"><strong>6 players</strong><span>Two teams of 3, nine cards each</span></button>
        <button class="size-pick" data-cmd="host" data-size="8"><strong>8 players</strong><span>Two teams of 4, six or seven cards each</span></button></div></section>
    <section><h2>Players nearby</h2>
      ${peers ? `<div class="peers">${peers}</div>`
        : '<p class="quiet-text">Nobody yet. Players running Fish on the same network show up here.</p>'}</section>
  </div>`;
}

function phoneHome() {
  const room = snap.rooms[0];
  const host = esc(snap.host_name);
  let what;
  if (!room) what = `<p class="quiet-text">Waiting for ${host} to open a room on the laptop.</p>`;
  else if (room.started) what = `<p class="quiet-text">${host}'s game has already started. You can join the next one.</p>`;
  else what = `<div><button class="go" data-cmd="join" data-host="${esc(room.host)}">Join ${host}'s table</button></div>`;
  return `<div class="home"><header class="hero">${LOGO}<h1>Fish</h1>
    <p class="lede">You're <b>${esc(snap.me.name)}</b>.</p></header><!--error-->${what}</div>`;
}

function qrPanel() {
  if (!snap.hosting || !snap.phone_url) return "";
  return `<aside class="qr">
    <img src="/qr.svg" alt="QR code to join this game from a phone" width="160" height="160">
    <div><h3>Play from a phone</h3>
      <p class="quiet-text">Scan this with the phone's camera. The phone has to be on the same Wi-Fi as this laptop.</p>
      <p class="url">${esc(snap.phone_url)}</p></div></aside>`;
}

function joining() {
  const room = snap.rooms.find((r) => r.host === snap.joined);
  return `<div class="home"><header class="hero">${LOGO}<h1>Fish</h1></header><!--error-->
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

// A seat's disc: initial, name, and a line under it (team, card count...).
function disc(name, line, { you = false, tag = "", extra = "" } = {}) {
  const bot = isBotName(name);
  return `<div class="disc">${tag}<span class="avatar">${esc(initial(name))}</span>
    <span class="who"><span class="name">${esc(bare(name))}${you ? ' <span class="me">you</span>' : ""}${bot ? ' <span class="bot-tag">bot</span>' : ""}</span>
    <span class="count">${line}</span>${extra}</span></div>`;
}

function lobby() {
  const L = snap.lobby;
  const me = snap.me.address;
  const isHost = snap.hosting;
  if (confirming !== "close") confirming = null;
  const mySeat = L.seats.findIndex((s) => s && s.address === me);
  const seated = L.seats.filter(Boolean).length;
  const seats = L.seats.map((s, i) => {
    const cls = `seat t${teamOf(i)} ${s ? "" : "empty"}`;
    let inner;
    if (s) {
      const bot = s.address.startsWith("bot:");
      inner = disc(s.name, s.away ? '<span class="away">Away</span>' : TEAM[teamOf(i)], { you: s.address === me })
        + (isHost && bot ? `<button class="quiet small" data-cmd="remove_bot" data-seat="${i}">Remove</button>` : "");
    } else {
      inner = `<div class="disc"><span class="avatar">+</span><span class="who"><span class="name">Empty seat</span>
        <span class="count">${TEAM[teamOf(i)]}</span></span></div>
        <div class="seat-actions"><button class="small" data-cmd="sit" data-seat="${i}">Sit here</button>
        ${isHost ? `<button class="quiet small" data-cmd="add_bot" data-seat="${i}">Add bot</button>` : ""}</div>`;
    }
    return `<div class="${cls}" style="${seatPos(i, Math.max(mySeat, 0), L.size)}">${inner}</div>`;
  }).join("");
  // The host running the table from a laptop is not waiting for a seat.
  const standing = L.members.filter((m) => m.address !== L.host && !L.seats.some((s) => s && s.address === m.address));
  const full = seated === L.size;
  const actions = `${mySeat >= 0 ? '<button class="quiet" data-cmd="stand">Stand up</button>' : ""}
    ${isHost ? `<button class="go" data-cmd="start" ${full ? "" : "disabled"}>Deal the cards</button>${hostButton("close", "Close room")}`
      : '<button class="quiet" data-cmd="leave">Leave room</button>'}`;
  const status = full ? (isHost ? "Everyone's seated. Deal when you're ready." : `Waiting for ${esc(hostName())} to deal.`)
    : isHost && snap.phone_url ? "Players scan the QR code, then pick a seat."
    : "Pick a seat. The seat's colour is your team.";
  const legend = `<div class="legend"><span class="t0">${TEAM[0]}</span><span class="t1">${TEAM[1]}</span>
    <p>Teammates sit in every other seat, so each team faces the other round the table.</p></div>`;
  return `${topBar(`${esc(hostName())}'s room`, `${seated} of ${L.size} seats filled`, actions)}
    ${confirmBar()}<!--error-->
    <div class="play">
      <div class="table"><div class="felt"></div>${seats}
        <div class="pond">${fishes({}, new Set())}
          <p class="pond-status">${status}</p></div></div>
      <aside class="side">
        ${qrPanel()}
        <section class="card-panel">${legend}
          ${standing.length ? `<p class="standing"><span class="label">Still standing</span>${standing.map((m) => esc(m.name)).join(", ")}</p>` : ""}
        </section>
      </aside>
    </div>`;
}

function game() {
  const v = snap.game;
  const names = seatNames();
  const away = (snap.lobby?.seats || []).map((s) => Boolean(s && s.away));
  const watching = v.you === null;
  const me = watching ? 0 : v.you;
  const myTeam = v.team;
  const myTurn = !watching && v.turn === me && !v.over;
  const teamTurn = !watching && teamOf(v.turn) === myTeam && !v.over;
  const isHost = snap.hosting;
  if (v.over && confirming !== "close") confirming = null;

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
  const moved = noteMove(v);
  document.title = myTurn ? "Your turn · Fish" : "Fish";

  const asking = myTurn && !v.must_pass && ui.mode === "ask";
  const said = bubbles(v, names);
  const seats = names.map((name, i) => {
    const can = asking && teamOf(i) !== myTeam && v.counts[i] > 0;
    const turn = v.turn === i && !v.over;
    const cls = `seat t${teamOf(i)} ${turn ? "turn" : ""} ${ui.target === i ? "target" : ""} ${can ? "can" : ""}
      ${v.counts[i] === 0 ? "out" : ""} ${seatXY(i, me, v.players).y < 30 ? "top" : ""}`;
    const delta = seen.deltas[i]
      ? `<span class="delta ${seen.deltas[i] > 0 ? "up" : "down"} ${moved ? "fresh" : ""}">${seen.deltas[i] > 0 ? "+" : "−"}${Math.abs(seen.deltas[i])}</span>`
      : "";
    const line = `<span class="backs" aria-hidden="true"></span>${plural(v.counts[i], "card")}${delta}`;
    const tag = turn ? `<span class="turn-tag">${i === v.you ? "Your turn" : "Their turn"}</span>` : "";
    const inner = disc(name, line, { you: i === v.you, tag, extra: away[i] ? '<span class="away">Away</span>' : "" });
    const body = can
      ? `<button class="pick" data-cmd="target" data-seat="${i}" aria-label="Ask ${esc(name)}">${inner}</button>`
      : inner;
    const bubble = said[i] ? `<div class="bubble ${said[i].tone} ${moved ? "fresh" : ""}">${said[i].text}</div>` : "";
    return `<div class="${cls}" style="${seatPos(i, me, v.players)}">${bubble}${body}</div>`;
  }).join("");

  const [g, t] = v.score;
  const middle = v.over
    ? `<p class="winner t${v.winner}">${TEAM[v.winner]} wins</p><p class="pond-status">${Math.max(g, t)} sets to ${Math.min(g, t)}</p>`
    : `<p class="pond-status">${plural(9 - g - t, "set")} still in play</p>`;
  const pond = `<div class="pond">${fishes(v.claimed, fresh)}${middle}</div>`;

  let controls = "";
  if (isHost) {
    controls = v.over
      ? `<button class="go" data-cmd="start">Play again</button>
         <button class="quiet" data-cmd="cancel">Back to the room</button>${hostButton("close", "Close room")}`
      : `${hostButton("restart", "New game")}${hostButton("cancel", "End game")}`;
  } else if (v.over) {
    controls = '<button class="quiet" data-cmd="leave">Leave room</button>';
  }
  const mates = names.filter((_, i) => teamOf(i) === myTeam && i !== me).map((n) => esc(bare(n))).join(" and ");
  const sub = watching ? "This screen is the table. Everyone's cards stay on their own screen."
    : `You're on <b class="n t${myTeam}">${TEAM[myTeam]}</b> with ${mates}.`;
  const now = `<p class="prompt ${myTurn ? "mine" : ""}">${prompt(v, names, myTurn, teamTurn, away)}</p>`;

  return `${topBar(v.over ? "Game over" : "Fish", sub, controls)}${confirmBar()}<!--error-->
    <div class="screen"><div class="play ${watching ? "watching" : ""}">
      <div class="table"><div class="felt"></div>${askLine(v, me)}${pond}${seats}</div>
      <aside class="side">
        ${watching ? `<section class="now t${teamOf(v.turn)}">${now}</section>` : ""}
        ${scoreboard(v, names)}
        ${playByPlay(v, names, moved)}
      </aside>
    </div>
    ${watching ? "" : `<section class="you t${myTeam} ${myTurn ? "my-turn" : ""}">
      <header>${now}</header>
      ${v.over ? "" : hand(v, moved)}
      ${v.over ? "" : actions(v, names, myTurn, teamTurn)}
    </section>`}</div>`;
}

// Each team: its score, its players, and the sets it has won.
function scoreboard(v, names) {
  const team = (k) => {
    const won = Object.keys(v.claimed).filter((id) => v.claimed[id] === k);
    const who = names.map((n, i) => [n, i]).filter(([, i]) => teamOf(i) === k)
      .map(([n, i]) => (i === v.you ? "you" : esc(bare(n)))).join(", ");
    return `<div class="team t${k} ${v.over && v.winner === k ? "winner" : ""}">
      <div class="team-head"><span class="team-name">${TEAM[k]}</span><span class="team-score">${v.score[k]}</span></div>
      <p class="team-who">${who}</p>
      <div class="won">${won.length ? won.map((id) => `<span class="won-set">${SHORT[id]}</span>`).join("")
        : '<span class="none">No sets yet</span>'}</div></div>`;
  };
  return `<section class="scoreboard" aria-label="Score">${team(0)}${team(1)}</section>`;
}

// Records a move this page hasn't shown yet. True when there is one.
function noteMove(v) {
  if (seen.moves === v.moves) return false;
  const first = seen.moves === null || v.moves < seen.moves;
  seen.deltas = first ? [] : v.counts.map((c, i) => c - (seen.counts?.[i] ?? c));
  seen.newCards = first || !seen.hand ? new Set() : new Set(v.hand.filter((c) => !seen.hand.includes(c)));
  seen.moves = v.moves;
  seen.counts = v.counts;
  seen.hand = v.hand;
  return !first;
}

// A player's name for the words under the table: "You" for you, in team colour.
function who(v, names, i, you = "You") {
  return `<b class="n t${teamOf(i)}">${i === v.you ? you : esc(names[i])}</b>`;
}

function chip(c) {
  const red = /[HD]$/.test(c) ? "red" : "";
  return `<span class="chip-card ${red}">${esc(cardName(c))}</span>`;
}

// What each seat just said, as speech bubbles on the table.
function bubbles(v, names) {
  const m = v.last_move;
  const said = {};
  if (!m || v.over) return said;
  if (m.kind === "ask") {
    said[m.asker] = { tone: "ask", text: `Got the ${esc(cardName(m.card))}?` };
    said[m.target] = m.got ? { tone: "yes", text: "Yes, here" } : { tone: "no", text: "No" };
  } else if (m.kind === "declare") {
    said[m.declarer] = { tone: m.correct ? "yes" : "no",
      text: `${esc(snap.set_names[m.set_id])}: ${m.correct ? "right" : "wrong"}` };
  } else if (m.kind === "pass") {
    said[m.player] = { tone: "ask", text: `Over to ${esc(names[m.to])}` };
  }
  return said;
}

// The latest ask, drawn from asker to target across the felt.
function askLine(v, me) {
  const m = v.last_move;
  if (!m || m.kind !== "ask" || v.over) return "";
  const a = seatXY(m.asker, me, v.players);
  const b = seatXY(m.target, me, v.players);
  return `<svg class="ask-line ${m.got ? "yes" : "no"}" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" vector-effect="non-scaling-stroke"/></svg>`;
}

// What just happened, in words: the latest move large, the one before small.
function playByPlay(v, names, moved) {
  const m = v.last_move;
  let head;
  let then = "";
  if (!m) {
    head = `<p class="what">${who(v, names, v.turn)} ${v.turn === v.you ? "go" : "goes"} first.</p>`;
  } else if (m.kind === "ask") {
    head = `<p class="what">${who(v, names, m.asker)} asked ${who(v, names, m.target, "you")} for the ${chip(m.card)}
      <span class="verdict ${m.got ? "yes" : "no"}">${m.got ? "Got it" : "Didn't have it"}</span></p>`;
    if (!v.over) {
      if (m.got) {
        then = m.asker === v.you ? "It's yours now, and you ask again."
          : `${who(v, names, m.asker)} takes it and asks again.`;
      } else {
        then = m.target === v.you ? "No card changes hands. The turn passes to you."
          : `No card changes hands. The turn passes to ${who(v, names, m.target)}.`;
      }
    }
  } else if (m.kind === "declare") {
    head = `<p class="what">${who(v, names, m.declarer)} declared ${esc(snap.set_names[m.set_id])}
      <span class="verdict ${m.correct ? "yes" : "no"}">${m.correct ? "All six right" : "Got it wrong"}</span></p>`;
    then = `<b class="n t${m.team}">${TEAM[m.team]}</b> wins the set, and its cards leave play.`;
  } else {
    head = `<p class="what">${who(v, names, m.player)} ${m.player === v.you ? "are" : "is"} out of cards
      and handed the turn to ${who(v, names, m.to, "you")}.</p>`;
  }
  // Earlier moves still on record: the asks before the latest, and the last declaration.
  const asks = v.history.slice(0, m && m.kind === "ask" ? -1 : undefined).reverse();
  const earlier = asks.map((a) => `<li>${who(v, names, a.asker)} asked ${who(v, names, a.target, "you")}
    for the ${esc(cardName(a.card))}: ${a.got ? "got it" : "no"}.</li>`);
  const d = v.last_declaration;
  if (d && m?.kind !== "declare") {
    earlier.push(`<li>${who(v, names, d.declarer)} declared ${esc(snap.set_names[d.set_id])}
      ${d.correct ? "right" : "wrong"}: ${TEAM[d.team]} won it.</li>`);
  }
  return `<section class="pbp ${moved ? "fresh" : ""}" aria-label="What just happened">
    <div class="latest"><span class="label">Just now</span>${head}${then ? `<p class="then">${then}</p>` : ""}</div>
    ${earlier.length ? `<div class="earlier"><span class="label">Before that</span><ul>${earlier.join("")}</ul>
      <p class="hint">Only the last two asks stay on record. Remembering the rest is up to you.</p></div>` : ""}
  </section>`;
}

function prompt(v, names, myTurn, teamTurn, away) {
  if (v.over) {
    const [g, t] = v.score;
    const verdict = v.you === null ? "" : v.winner === v.team ? " Well played." : " Next time.";
    return `${TEAM[v.winner]} wins, ${Math.max(g, t)} sets to ${Math.min(g, t)}.${verdict}`;
  }
  if (myTurn && v.must_pass) return "Your turn, but you're out of cards. Pick a teammate to take it.";
  if (myTurn) return "Your turn. Ask an opponent for a card, or declare a set.";
  const whose = `<b class="n t${teamOf(v.turn)}">${esc(names[v.turn])}</b>`;
  const side = v.you === null ? ` (${TEAM[teamOf(v.turn)]})`
    : teamTurn ? " (your team)" : " (other team)";
  if (away[v.turn]) return `${whose}'s turn, but they're out of range. The game waits for them.`;
  if (teamTurn) return `${whose}'s turn${side}. You can still declare a set for your team.`;
  return `${whose}'s turn${side}. Wait for them to ask.`;
}

function hand(v, moved) {
  if (!v.hand.length) return '<p class="quiet-text">You have no cards left. You can still declare on your team\'s turn.</p>';
  const groups = {};
  for (const c of v.hand) {
    const id = Object.keys(snap.sets).find((s) => snap.sets[s].includes(c));
    (groups[id] ||= []).push(c);
  }
  return `<div class="hand">${Object.entries(groups).map(([id, cards]) =>
    `<div class="group"><span class="label">${esc(snap.set_names[id])}</span>
      <div class="cards">${cards.map((c) => cardHtml(c, seen.newCards.has(c) ? `new ${moved ? "fresh" : ""}` : "")).join("")}</div></div>`).join("")}</div>`;
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
    case "confirm": confirming = confirming === d.what ? null : d.what; break;
    case "unconfirm": confirming = null; break;
    case "close": confirming = null; render(); return send({ cmd: "close" });
    case "restart": confirming = null; render(); return send({ cmd: "restart" });
    case "cancel": confirming = null; render(); return send({ cmd: "cancel" });
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
