
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { Check } from "lucide-react";

/* ══ Todo tower ════════════════════════════════════════════
   A list stacked like bricks, under gravity. Check one off and
   it is pulled out; take one out by hand and the rest come
   down after it, and some of them do not land back on the
   pile. Pick anything up off the floor and put it back.

   ── THERE ARE NO SLOTS ANY MORE, AND THAT IS THE REWRITE ──
   The first three versions of this block were a column of
   numbered positions with an offset per card: a row was "slot
   4, sitting 12px above where it belongs". That model can say
   everything about a tidy stack and nothing at all about a
   card that has left it. A slot is a promise that the card
   comes back to the column, and the whole of what was asked
   for here is cards that do not.

   So a card is now a BODY — x, y, and the speed of each — in a
   box with a floor. Nothing knows what a tower is. A tower is
   what you get when twelve bodies are resting on each other
   near the middle, and a mess is what you get when they are
   not, and neither is a state the code holds.

   Everything else fell out of that rather than being built:

   · Pull one out and the ones above lose their support, so
     they fall. Nobody tells them to.
   · They land crooked, and a card that lands crooked enough
     slides off the one under it and drops to whatever is
     below — which may be the floor.
   · A card on the floor is not a special kind of card. It is a
     body whose support happens to be the bottom of the box.
   · Drop one anywhere and it lands on whatever is under it.
     The old version could only ever put a card back on TOP,
     because a slot model has nowhere else to put it.

   ── IT IS STILL NOT A SPRING ─────────────────────────────
   A spring is a force proportional to the distance left to
   travel: slow, fast, slow. A falling object has no idea where
   it is going — constant acceleration the whole way down, so
   it is slowest at the start and FASTEST as it lands. Slow-in
   reads as "the row animated down". Hitting hardest at the
   bottom reads as "it fell".

   ── AND THE CARD HAD TO GET NARROWER ─────────────────────
   It was the full width of the block, which is why none of
   this could have worked before: a card that cannot move
   sideways past its neighbour cannot fall off it. At half the
   block's width two cards can sit side by side on the floor,
   and a card can slide until it has nothing under it. The
   block got wider at the same time, which the wall prefers
   anyway — 340x360 is nearly square where 250x417 was a
   ribbon. */

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/* ── the box and the brick ─────────────────────────────────
   The card is exactly half the block, so two of them fit side
   by side on the floor with nothing to spare. That is not a
   coincidence, it is the number chosen: it is the widest a
   card can be and still be able to get completely out of the
   way of another one. */
const W = 340;
const CW = 170;
const CH = 30;
const MAX = 12;

/* and the block is the full tower, exactly — twelve bricks
   resting on each other with no air between them. There are no
   gaps in a stack; the gap was a slot model's idea of one. */
const H = MAX * CH;

/* how far from the middle a card can get before the box stops
   it. The block clips, so this is not a preference. */
const SIDE = (W - CW) / 2;

/* gravity, px/s². Real units, because the integrator is real:
   the drop time falls out of it rather than being tuned. */
const grav = (v: number) => 900 + (clamp(v, 0, 100) / 100) * 3300;

/* ── how much of its speed a card keeps off the floor ──────
   Restitution, and the knob is NOT linear in it. What you can
   see is how HIGH the card comes back, and height goes as the
   square of restitution: apex = (e·v)²/2g. Square-rooting puts
   the knob on the visible thing rather than on the coefficient
   underneath it. Bounce sets the SQUASH too, because a card
   that keeps half its speed off the floor is made of something
   springy and something springy deforms when it lands. */
const REST = 0.5;
const restitution = (v: number) => REST * Math.sqrt(clamp(v, 0, 100) / 100);

/* below this, in px/s, a landing is a landing rather than a
   rebound — without it a card bounces forever in increments
   nobody can see and the loop never parks */
const VSTOP = 38;
/* the impact speed at which squash and slide are at their
   fullest */
const VREF = 1500;

/* ── WHEN A CARD IS NO LONGER SITTING ON ANYTHING ──────────
   The share of its width that has to be over its support for
   it to stay there. Above this it rests; below it, it starts
   sliding off, and the further off it gets the harder it goes
   — which is why a card that is nearly clear leaves in a
   hurry rather than creeping.

   A HALF is the honest number and it is the floor of the range,
   not the whole of it: a rigid card on a rigid card tips when
   its centre of mass passes the edge of what it is standing
   on, which is exactly half its width. Everything above that
   is the surface being slippery rather than the card being
   unbalanced — which is what Slip is, so Slip sets it.

   At 0 a card has to be half off before it goes, and nothing
   this block does to a tower will ever push one that far; the
   stack is safe. At 100 a third off is enough and a bad
   collapse loses cards over the side. Measured before this was
   a range: at a flat 0.62 a card needed 65px of overhang and
   the hardest landing in the block moved it nine, so nothing
   ever fell off at any setting — the feature was there and
   unreachable. */
const stability = (v: number) => 0.5 + (clamp(v, 0, 100) / 100) * 0.32;
const SLIDE = 1100;

/* the sideways shove a landing gives, and what takes it away.
   FRICTION is what is left of the lateral speed after a
   sixtieth of a second — only about an eighth of a launch
   survives as distance, which is why SHOVE is this big. */
const SHOVE = 820;
const FRICTION = 0.86;

/* ── and pulling one out SCRAPES the ones above it ─────────
   A brick does not leave a stack politely. Checking a row off
   and taking one in your hand both take a card out from under
   whatever was resting on it, and the cards above should feel
   that as more than the loss of a floor — so they get a shove
   at the moment it goes, dying away with distance up the pile.

   This is the one impulse in the block that is not a landing,
   and it is what gives a drag its consequences: a card three
   rows up gets a nudge, and a nudge on a stack that is already
   leaning is what starts one over the edge. */
const SCRAPE = 300;

/* ── carrying one ──────────────────────────────────────────
   GRAB is how far the pointer has to travel before a press
   becomes a lift: the bench's own drag test, and what leaves a
   plain press to the checkbox under it. VMAX is what a
   released card is allowed to arrive with, so a hard flick
   cannot fire it through the floor in one step. */
const GRAB = 4;
const VMAX = 2200;

/* ── the offsets the tower starts with ─────────────────────
   Written down rather than rolled, and the distinction matters
   here in a way it does not for the landings. A wall can hold
   several copies of this block and they must all draw the same
   tower standing still; what happens AFTER you knock it is a
   physical event and should not repeat. */
const LEAN = [
  -0.42, 0.68, -0.15, 0.51, -0.86, 0.24, 0.73, -0.57, 0.12, -0.7, 0.38, -0.29,
];

const TODOS = [
  "Reply to Nadia",
  "Renew the domain",
  "Book the flights",
  "Export the icons",
  "Pay the invoice",
  "Cancel the trial",
  "File the receipts",
  "Call the landlord",
  "Back up the drive",
  "Order more coffee",
  "Fix the door handle",
  "Water the plants",
];

type TodoItem = { id: string | number; text: string };

/* x and y are the card's own place in the box — y is its
   BOTTOM edge above the floor, so a card on the floor is at 0
   and the stack is just a column of them. `was` is last
   frame's y, and it is what stops a fast card falling straight
   through the one below it. */
type Body = { id: number; x: number; y: number; was: number; vx: number; vy: number; sq: number };

const stack = (n: number, slip: number): Body[] =>
  Array.from({ length: n }, (_, i) => ({
    id: i,
    /* TODOS[0] reads first, so it is the card on TOP */
    y: (n - 1 - i) * CH,
    was: (n - 1 - i) * CH,
    x: clamp(LEAN[i % LEAN.length] * slip * 20, -SIDE, SIDE),
    vx: 0,
    vy: 0,
    sq: 0,
  }));

/* ── inlined from ./spring ──────────────────────── */
/* ── one spring, for everything that settles ───────────────
   The maths was already on this bench twice, copied by hand:
   Humidity's wheel and Brightness's column both accumulate
   velocity toward a target, damp it, and snap when both the
   delta and the velocity fall under 0.02. Two copies is a
   coincidence; five would be a policy, so it comes out here
   before the elastic blocks are written against it.

   The two shipped copies are deliberately NOT refactored onto
   this. They work, they are tuned, and rewriting the innards
   of two live components to prove a point about duplication
   is how a good afternoon becomes a bad one. This is the one
   new code uses.

   Frames, not milliseconds. `dt` is expressed in sixtieths of
   a second and the damping is RAISED to it rather than
   multiplied by it, so a dropped frame decays the same amount
   of energy as the two frames it replaced. Multiplying is the
   version that makes a spring behave differently on a busy
   page, which is the hardest kind of bug to see.

   The loop parks itself the moment the value has settled.
   CLAUDE.md is not complimentary about the one permanent
   requestAnimationFrame already on this bench and there is no
   case for five more. */

/* 0..100 into the two numbers a spring actually has.

   50 is what Humidity and Brightness were tuned at, which is
   the rule every elastic knob on this bench follows — see
   lab/motion. Turn the panel to the middle and nothing has
   changed.

   Both ends have to be usable, which is what fixes the range:
   at 0 it is slow and heavy and still arrives, at 100 it is
   quick with a visible overshoot, and nowhere in between does
   it ring for longer than it takes to read. */
/* The pair is chosen by DAMPING RATIO and then written back
   as stiffness and decay, because the ratio is the thing a
   person is actually setting and the two numbers on their own
   do not say what they add up to.

     zeta = -ln(d) / (2 * sqrt(k))

   The first version of this ran 0.06..0.26 stiffness against
   0.93..0.74 decay, which reads as a sensible spread and is
   not one: it puts zeta between 0.15 and 0.16 across the
   WHOLE range, so every setting overshot by about sixty per
   cent and the knob only changed how fast it did it. Pull's
   return went 130px past its own resting position and lifted
   the content off the top of the card.

     0   → zeta ~0.85, heavy, arrives without a ring
     50  → zeta ~0.41, near where Humidity and Brightness sit
     100 → zeta ~0.20, lively, two visible rebounds

   Both ends shippable, which is the constraint that fixed the
   numbers rather than taste. */
const springOf = (tune: number) => ({
  /* stiffness: how hard it is pulled toward the target */
  k: 0.08 + (tune / 100) * 0.16,
  /* decay, per frame: how much of the velocity survives */
  d: 0.62 + (tune / 100) * 0.2,
});

/* Units matter. The snap threshold is absolute, so a caller
   works in pixels or in 0..100 — a spring driven over 0..1
   would be "settled" before it had visibly moved. */
function useSpring(target: number, tune = 50, instant = false) {
  const [at, setAt] = useState(target);
  const cur = useRef(target);
  const vel = useRef(0);
  const raf = useRef(0);

  useEffect(() => {
    if (instant) {
      cur.current = target;
      vel.current = 0;
      setAt(target);
      return;
    }
    const { k, d } = springOf(tune);
    let prev = 0;
    const tick = (t: number) => {
      const dt = prev ? clamp((t - prev) / 16.67, 0, 2.5) : 1;
      prev = t;
      vel.current += (target - cur.current) * k * dt;
      vel.current *= Math.pow(d, dt);
      cur.current += vel.current * dt;
      if (Math.abs(target - cur.current) < 0.02 && Math.abs(vel.current) < 0.02) {
        cur.current = target;
        vel.current = 0;
        setAt(target);
        raf.current = 0;
        return;
      }
      setAt(cur.current);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf.current);
      raf.current = 0;
    };
    /* `tune` sits here beside `target` for the reason
       Brightness spells out: the loop closes over it, so
       without it a knob turned mid-flight would do nothing
       until something else restarted the effect. Restarting
       picks up from the refs, so it continues rather than
       snapping. */
  }, [target, tune, instant]);

  return at;
}

/* Read once, the way the wheel and the pill nav do. A
   preference, not a live input. */
const stillness = () =>
  typeof window !== "undefined" &&
  !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function TodoTower({
  items: controlledItems,
  onComplete,
  /* how hard it falls, 0..100 */
  gravity = 50,
  /* how much it keeps off the floor, 0..100 — and the squash */
  bounce = 50,
  /* how far off-centre it sits, and how far a landing slides */
  slip = 55,
  /* cards in the tower, 6..12 */
  count = MAX,
}: {
  items?: TodoItem[];
  onComplete?: (id: TodoItem["id"]) => void;
  gravity?: number;
  bounce?: number;
  slip?: number;
  count?: number;
} = {}) {
  const todoItems = controlledItems ?? TODOS.map((text, id) => ({ id, text }));
  const n = clamp(Math.round(controlledItems ? controlledItems.length : count), 1, MAX);
  const stageH = Math.max(180, n * CH);
  const sl = clamp(slip, 0, 100) / 100;
  const still = stillness();

  const [cards, setCards] = useState<Body[]>(() => stack(n, clamp(slip, 0, 100) / 100));
  /* the one being checked off, on its way out of the block. It
     is out of the world already — the tower closes over it
     while it is still on screen. */
  const [going, setGoing] = useState<Body | null>(null);
  /* the one in your hand. Out of the world too: it falls on
     nothing and nothing rests on it. */
  const [hand, setHand] = useState<number | null>(null);

  const world = useRef<Body[]>(cards);
  const held = useRef<number | null>(null);
  const grip = useRef<{
    id: number;
    ox: number;
    oy: number;
    bx: number;
    by: number;
    vx: number;
    vy: number;
    t: number;
    live: boolean;
    k: number;
  } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const raf = useRef(0);
  const running = useRef(false);
  /* one knock per collapse. Eleven cards landing in the same
     frame is one sound in the world. */
  const landed = useRef(false);

  /* either knob rebuilds the tower rather than adjusting it:
     a card that vanished because a slider moved would miss the
     fall, and the fall is what this block is */
  useEffect(() => {
    const fresh = stack(n, sl);
    world.current = fresh;
    setCards(fresh);
    setGoing(null);
    setHand(null);
    held.current = null;
    grip.current = null;
  }, [n, sl]);

  /* ── what is under a card ─────────────────────────────────
     The highest thing it overlaps that is not above it. The
     test uses LAST frame's y and not this one's, which is the
     whole of how a fast card is stopped: at the speeds this
     reaches, one step is longer than a card is tall, so a
     card that had cleared a top by the end of a frame would
     find no support there and fall straight through it. Asking
     where it was, rather than where it got to, catches it. */
  const under = (b: Body, all: Body[]) => {
    let top = 0;
    let on: Body | null = null;
    for (const o of all) {
      if (o.id === b.id || o.id === held.current) continue;
      if (Math.abs(o.x - b.x) >= CW) continue;
      const t = o.y + CH;
      if (t <= b.was + 1 && t > top) {
        top = t;
        on = o;
      }
    }
    return { top, on };
  };

  const start = useCallback(
    (from?: Body[]) => {
      if (from) world.current = from;
      if (still) {
        setCards([...world.current]);
        return;
      }
      if (running.current) return;
      running.current = true;
      landed.current = false;
      let prev = 0;
      const acc = grav(gravity);
      const e = restitution(bounce);
      const dent = 0.3 + (clamp(bounce, 0, 100) / 100) * 0.7;
      const grip0 = stability(slip);

      const tick = (t: number) => {
        /* seconds, capped: a tab that was in the background for
           a second must not integrate a second of gravity in
           one step */
        const dt = prev ? clamp((t - prev) / 1000, 0, 1 / 30) : 1 / 60;
        prev = t;
        const all = world.current;
        let busy = false;

        /* LOWEST FIRST, so a card always resolves against a
           support that has already moved this frame */
        const order = [...all].sort((a, b) => a.y - b.y);

        for (const b of order) {
          if (b.id === held.current) continue;
          const { top, on } = under(b, all);

          b.was = b.y;
          if (b.y > top || b.vy < 0) {
            b.vy += acc * dt;
            b.y -= b.vy * dt;
          }

          if (b.y <= top) {
            const hit = b.vy;
            b.y = top;
            /* contact is only contact when it is falling INTO
               the thing it touches. A card resting on another
               sits at exactly its support, so the test fires
               every frame it is there — including the frames
               where the support has bounced and is carrying it
               upward, and zeroing the velocity then kills the
               rebound one frame after starting it. */
            if (hit > VSTOP) {
              const force = clamp(hit / VREF, 0, 1);
              b.vy = -hit * e;
              b.sq = Math.max(b.sq, force * dent);
              /* rolled, not seeded: a physical event that
                 repeats exactly is what gives the game away */
              b.vx += (Math.random() * 2 - 1) * force * sl * SHOVE;
              if (!landed.current) {
                landed.current = true;
                }
            } else if (hit > 0) {
              b.vy = 0;
            }

            /* ── and then it may not stay there ──────────────
               How much of it is actually over the thing it is
               standing on. Past the tipping point it slides,
               and the further off it gets the harder it goes —
               so a card that is nearly clear leaves in a hurry
               rather than creeping off. */
            if (on) {
              const over = 1 - Math.abs(b.x - on.x) / CW;
              if (over < grip0) {
                b.vx += Math.sign(b.x - on.x || 1) * SLIDE * (grip0 - over) * dt;
              }
            }
          }

          /* the slide, and what stops it */
          if (b.vx !== 0) {
            b.x = clamp(b.x + b.vx * dt, -SIDE, SIDE);
            b.vx *= Math.pow(FRICTION, dt * 60);
            if (Math.abs(b.vx) < 1 || Math.abs(b.x) >= SIDE) b.vx = 0;
          }

          b.sq *= Math.pow(0.86, dt * 60);
          if (b.y > top + 0.15 || Math.abs(b.vy) > 0.5 || b.vx !== 0 || b.sq > 0.004) busy = true;
        }

        setCards([...all]);
        if (busy) {
          raf.current = requestAnimationFrame(tick);
          return;
        }
        for (const b of all) {
          b.vy = 0;
          b.vx = 0;
          b.sq = 0;
          b.was = b.y;
        }
        setCards([...all]);
        running.current = false;
        raf.current = 0;
      };
      raf.current = requestAnimationFrame(tick);
    },
    [bounce, gravity, sl, still],
  );

  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      running.current = false;
    },
    [],
  );

  /* ── the list fills itself back up ────────────────────────
     A block that can be emptied and not filled works once, and
     the wall plays every card's demo unattended. */
  useEffect(() => {
    if (cards.length > 0 || going || hand !== null) return;
    const wait = window.setTimeout(() => {
      const fresh = stack(n, sl);
      fresh.forEach((b, i) => {
        b.y = stageH - CH + 10 + i * 8;
        b.was = b.y;
      });
      setCards(fresh);
      start(fresh);
    }, 600);
    return () => clearTimeout(wait);
  }, [cards, going, hand, n, sl, stageH, start]);

  /* ── what leaving does to the rest ───────────────────────
     Losing the floor is most of it and it is not all of it: a
     brick does not come out of a stack politely. Everything
     above the one that left gets a shove as it goes, dying
     away with height, which is what turns a drag into a
     consequence rather than a subtraction. */
  const scrape = (gone: Body) => {
    for (const c of world.current) {
      if (c.id === gone.id || c.y <= gone.y) continue;
      const far = Math.max(1, (c.y - gone.y) / CH);
      c.vx += (Math.random() * 2 - 1) * sl * SCRAPE * (1 / far);
    }
  };

  const strike = (id: number) => {
    if (going || hand !== null) return;
    const b = world.current.find((c) => c.id === id);
    if (!b) return;
    setGoing({ ...b });
    scrape(b);
    /* out of the world: everything resting on it loses its
       support this frame and starts falling. Nobody is told to
       fall — there is simply nothing under them. */
    start(world.current.filter((c) => c.id !== id));
    window.setTimeout(() => setGoing((cur) => (cur?.id === id ? null : cur)), still ? 0 : 300);
    if (onComplete) window.setTimeout(() => onComplete(todoItems[id].id), still ? 0 : 320);
  };

  /* ── the hand ─────────────────────────────────────────────
     Nothing happens on press. The lift waits for GRAB pixels of
     travel, which leaves a plain press to the checkbox under it
     — and the rehearsal depends on that too: a scripted demo
     presses and releases without moving. */
  const take = (e: PointerEvent, id: number) => {
    if (e.button !== 0 || going || hand !== null) return;
    if ((e.target as HTMLElement).closest(".twr-check")) return;
    const el = wrap.current;
    const b = world.current.find((c) => c.id === id);
    if (!el || !b) return;
    const box = el.getBoundingClientRect();
    grip.current = {
      id,
      ox: e.clientX,
      oy: e.clientY,
      bx: b.x,
      by: b.y,
      vx: 0,
      vy: 0,
      t: e.timeStamp,
      live: false,
      /* the stage scales the block, so a pointer moved by one
         screen pixel has moved by less than one of the
         component's own — the ball block's note */
      k: box.width / el.offsetWidth || 1,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const move = (e: PointerEvent) => {
    const g = grip.current;
    if (!g) return;
    const dx = (e.clientX - g.ox) / g.k;
    const dy = (e.clientY - g.oy) / g.k;
    if (!g.live) {
      if (Math.hypot(dx, dy) < GRAB) return;
      g.live = true;
      held.current = g.id;
      setHand(g.id);
      /* and the pile answers at once: whatever was resting on
         this card has nothing under it now, and got scraped on
         the way past */
      const b0 = world.current.find((c) => c.id === g.id);
      if (b0) scrape(b0);
      start();
    }
    const b = world.current.find((c) => c.id === g.id);
    if (!b) return;
    const dt = Math.max(1, e.timeStamp - g.t) / 1000;
    const nx = clamp(g.bx + dx, -SIDE, SIDE);
    const ny = clamp(g.by - dy, 0, stageH - CH);
    g.vx = (nx - b.x) / dt;
    g.vy = (b.y - ny) / dt;
    g.t = e.timeStamp;
    b.x = nx;
    b.y = ny;
    b.was = ny;
    b.vx = 0;
    b.vy = 0;
    setCards([...world.current]);
  };

  const drop = () => {
    const g = grip.current;
    grip.current = null;
    if (!g || !g.live) return;
    held.current = null;
    setHand(null);
    const b = world.current.find((c) => c.id === g.id);
    if (b) {
      /* where you let go is the drop and how fast you let go is
         added to it, so a card lobbed from the top of the block
         lands hard and skids and one set down gently does not.
         It lands on whatever is under it, which may be the
         tower, may be another card on the floor, and may be
         the floor. */
      b.vy = clamp(g.vy, -VMAX, VMAX);
      b.vx = clamp(g.vx, -VMAX, VMAX);
      b.was = b.y;
    }
    start();
  };

  const draw = (b: Body, out: boolean) => (
    <li
      key={b.id}
      className="twr-row"
      data-out={out || undefined}
      data-hand={hand === b.id || undefined}
      style={{
        width: CW,
        height: CH,
        /* the box's own floor is y = 0, so a card's y is how
           far its bottom edge is above it. Both axes are the
           loop's, and nothing in the stylesheet may touch this
           property. */
        transform: `translate(${b.x.toFixed(2)}px, ${(-b.y).toFixed(2)}px)`,
      }}
      onPointerDown={(e) => take(e, b.id)}
      onPointerMove={move}
      onPointerUp={drop}
      onPointerCancel={drop}
    >
      <div
        className="twr-card"
        style={{ scale: `${(1 + b.sq * 0.05).toFixed(4)} ${(1 - b.sq * 0.16).toFixed(4)}` }}
      >
        <button
          type="button"
          className="twr-check"
          aria-label={`Done: ${todoItems[b.id].text}`}
          onClick={() => strike(b.id)}
        >
          <Check size={9} strokeWidth={3.6} />
        </button>
        <span className="twr-text">{todoItems[b.id].text}</span>
      </div>
    </li>
  );

  return (
    <div className="twr" ref={wrap} style={{ width: W, height: stageH }}>
      <ul className="twr-pile">
        {/* by id, always: the cards are placed by their own
            coordinates, so the DOM order is free to be the one
            thing that does not change — React never reorders a
            node and a card in your hand keeps its identity
            through every collapse around it */}
        {[...cards].sort((a, b) => a.id - b.id).map((b) => draw(b, false))}
        {going ? draw(going, true) : null}
      </ul>
    </div>
  );
}
