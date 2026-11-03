# Debugging Ottobot on the mesh

When the bot "didn't answer", there are three possibilities. It never heard
the message. It answered, but the reply never reached the mesh. Or it
answered and the reply reached the mesh, but the person asking didn't receive
it. Two tools tell these apart:

- **Honeycomb** shows what the bot itself saw and did: every packet it heard,
  every dispatch and command, and every send.
- **Beacon** (https://analyzer.meshmapper.net/) shows what the rest of the
  mesh heard. It collects packets from observers (MQTT-connected nodes)
  around Ottawa (IATA `YOW`) and beyond.

Checking a message in both and lining them up usually answers the question.

## The packet hash ties the two together

Beacon identifies every packet by a hash of its payload:
`SHA-256(payload_type byte + payload)[:8]`, as hex (see
`src/ottobot/beacon.py`). The path isn't hashed, so every relayed copy of a
message has the same hash, whichever repeaters it came through. The firmware
uses the same hash to drop duplicate floods.

On the Honeycomb side:

- The `rx packet` and `dispatch` spans record it as `ottobot.packet_hash`.
- `!ping` replies include it as a short link: `https://m.tahnok.ca/<hash>`
  redirects to `https://analyzer.meshmapper.net/?hash=<hash>&analyze=1`
  (`deploy/short-links/Caddyfile`).
- The bot's **own** sends don't carry a hash (the `send` span only has the
  text). To find a reply on Beacon, look it up by channel and time (see
  below).

A resend from the retry in `runner._send_chan_msg` reuses the same timestamp
and text. That makes it the same packet with the same hash, so Beacon shows a
retried send as one packet, not two.

## Beacon API

Base URL: `https://analyzer.meshmapper.net/api/v1`. It's public, read-only
and unauthenticated, and needs no key. It's rate-limited to about 300
requests/min per IP. Full contract:
<https://github.com/MeshCore-Beacon/beacon-docs/blob/main/docs/api-contract.md>
(Swagger is at `/swagger/index.html` on the server).

API conventions:

- JSON keys are camelCase.
- **Times are epoch milliseconds**, both in responses and in `since`/`until`
  parameters.
- Lists are paged as `{items, nextCursor, hasMore}`. Pass `nextCursor` back
  as `cursor`. `limit` is capped at 200.
- `404` means the thing doesn't exist (for example, no observer heard that
  packet). `5xx` is a server problem and worth retrying.

### Look up one packet: who heard it, and by which route

```bash
curl -s https://analyzer.meshmapper.net/api/v1/packets/<hash> | jq
```

Useful fields:

- `observationCount` is how many observer hearings there were. For the bot's
  pongs that's typically about 18–20. **A `404` for a packet the bot sent
  means nobody heard it.** It was lost on the air, almost always by colliding
  at the bot's repeater.
- `firstHeardAt`, `lastHeardAt` and `firstToLastMs` show how long the flood
  ran (often 4–6 s). This is why `SEND_SPACING_SECONDS` and
  `REPLY_DELAY_SECONDS` in `runner.py` are as long as they are.
- `parsedPayload.decrypted` holds `{sender, content, sentAt}` for channels
  Beacon has the key for. `sentAt` comes from the sender's clock.
- `channelHash` is the channel's one-byte hash, which many channels share
  (see the table below).
- `observations[]` has one entry per hearing, with `observerName`, `heardAt`,
  `pathBytes`, `pathLength.hopCount`, `rssi`/`snr`, `propagationTimeMs`, and
  `resolvedPath` (path hashes resolved to named repeaters).

Every ottobot packet enters the mesh through one repeater, `309947…`
(`MrAlders0n_R4`). It's the first path hop in every observation of the bot's
packets and the last hop in the bot's own `ottobot.path`. If something the
bot sends collides there, nothing gets out.

### List a channel's messages

```bash
curl -s 'https://analyzer.meshmapper.net/api/v1/channels/<id>/messages?limit=200' | jq
```

This returns messages newest first. Each message has `packetHash`,
`senderName`, `content`, `sentAt` and `observationCount`. Page back with
`cursor` until you pass the time you're interested in.

Use the per-channel endpoint, not the global `GET /messages`. The global list
covers every channel worldwide, so paging back even a few hours takes dozens
of requests.

Beacon channel IDs for the bot's channels:

| Channel | Bot slot | `channelHash` | Beacon channel ID |
|---|---|---|---|
| `#testing` | 1 | `59` | `7650` |
| `#bots` | 4 | `44` | `19706207` |
| `#ottobot-testing` | 2 | `73` | not decrypted by Beacon |
| `#ott-alerts` | 3 | `4f` | not decrypted by Beacon |
| `public` | 0 | — | — |

Channel hashes collide: `44` is also `#rochester` and others, and `59` is
also `#emcomm` and others. To look a channel up by name, use
`GET /channels?hash=<hh>&keyKnown=true`.

For channels Beacon can't decrypt, you can still find the bot's packets by
time with `GET /packets?payloadTypeName=group_text&since=…&until=…`. They show
up as ciphertext only.

### Other endpoints worth knowing

- `GET /observers?iata=YOW` lists Ottawa observers, with `status`
  online/offline. Check it if a packet has suspiciously few hearings.
- `GET /observers/{id}/activity?range=24h` shows what one observer heard,
  bucketed by time.
- `GET /nodes?name=<partial>` and `GET /nodes/{id}/neighbors` show which
  repeaters a node is heard through.

## Honeycomb

The dataset is `ottobot`, in environment `test`. These spans are the useful
ones:

| Span | Key attributes |
|---|---|
| `rx packet` | `ottobot.packet_hash`, `ottobot.payload_type`, `ottobot.route_type`, `ottobot.path`, `ottobot.hops`, `ottobot.last_hop`, `ottobot.snr`, `ottobot.rssi`, `ottobot.raw` |
| `dispatch` (root) | `ottobot.packet_hash`, `ottobot.channel`, `ottobot.sender_name`, `ottobot.text`, `ottobot.outcome` (`replied`, `not_a_command`, …) |
| `command <name>` | `ottobot.command`, `ottobot.args` |
| `send` | `ottobot.channel_idx`, `ottobot.text`, `ottobot.send_wait_seconds`, `ottobot.attempts`, `ottobot.repeat_heard`, `ottobot.repeat_wait_seconds` |
| `task <name>` (root) | `ottobot.task` |

What the send attributes mean:

- `repeat_heard=true` means the bot's radio heard a repeater re-flood its own
  packet. That only proves the first hop: the bot to `309947`. The repeater's
  rebroadcast can still collide further out, so check Beacon. On 2026-10-14
  at 06:06 ET a `!roll` reply had `repeat_heard=true` but no observer heard
  it, because it went out at the same moment as an unrelated mesh-wide flood.
- `attempts=2` means the first copy went unheard and the bot sent it again.

**Check `service.instance.id` before blaming new code.** It changes on every
restart. If a `send` span lacks attributes that a recent change added, the old
build was still running. This happened with the fix in #122: it merged about
25 minutes before it was deployed.

## Worked example: "ottobot didn't answer my !ping"

1. Convert the reported time to UTC. Ottawa is UTC−4 during EDT and UTC−5
   during EST.
2. In Honeycomb, list raw `dispatch` and `send` spans for that window. Find
   the `dispatch` with the user's text and note its `ottobot.packet_hash`,
   `ottobot.outcome` and trace ID.
   - No `dispatch`: the bot never heard it. Look on Beacon at
     `/packets/<hash>` and the observations' paths to see whether the
     message reached a repeater near the bot.
   - `outcome` isn't `replied`: it's a dispatch problem (wrong channel, not
     addressed, unknown command).
3. Look at the trace's `send` span: `attempts`, `repeat_heard`, and the
   delay before sending.
4. On Beacon, list the channel's messages around that time and look for an
   `ottobot` message. If there isn't one, the reply never reached the mesh. If
   there is one, compare its `observationCount` to the ping's. When the reply
   reached the mesh but not the user, the problem is the route back to them,
   not the bot.

A short Python loop over `/channels/<id>/messages` that prints `sentAt`,
`packetHash`, `observationCount`, `senderName` and `content` for a time window
is usually enough. No client library is needed.
