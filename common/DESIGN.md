# XX design rules (one page)

Phone canvas: 360 px logical width. Gutter 20 px. Spacing scale 4/8/12/16/20/24/32.

## Color

Flat, monochrome per theme. Theme tokens only, never hard-coded colors —
except danger `#ff6b6b` (fill for End call / destructive confirm, text
`#ffffff`; red text for Missed/Rejected).

| Token | Use |
|---|---|
| `background` | whole window |
| `surface` | cards, keypad keys, inputs, popovers |
| `surface_alt` | avatar discs, pressed states |
| `border` | 1 px hairlines and outlines |
| `foreground` | primary text, filled/selected controls |
| `muted` | secondary text, captions, inactive tabs |
| `accent` | sparingly: links, active indicator when the theme defines one |

Selection is INVERTED: fill `foreground`, text `background` (ALL chip, FAB,
SPEAKER toggle, call button).

## Type

One family: the theme font (default JetBrainsMono Nerd Font). Regular 400
for nearly everything, medium 500 for row primary text at most. No bold
titles. Caps labels are uppercased in code.

| Role | Size | Notes |
|---|---|---|
| display (page title) | 30 px regular | left-aligned at gutter |
| display-large (detail/in-call name) | 32 px regular | |
| numeric-hero (dial number, timer) | 36 px light | tabular (monospace font) |
| title (row primary, field value) | 16 px | |
| body | 15 px | |
| secondary (row subtitle) | 14 px, `muted` | |
| caption | 11 px caps, `muted` | counts, field labels, section labels, day separators |
| chip label | 12 px caps | tab label 11 px caps |

## Shape

Rows min height 64 px; 1 px `border` hairline between rows (gutter to
gutter), none after the last. Radii: chips/segmented 3 px, outline
buttons and inputs 12 px, cards/dialogs 16 px, FAB/call button 20 px,
avatars circle, outgoing bubbles 4 px.

## Components

`PageHeader` (display title + caption + chromeless text actions, no
HeaderBar) and a compact back-chevron variant; `ListRow` (40 px avatar or
glyph, title + secondary, muted meta, pressed state); `Avatar`
(32/40/64/88); `ChipBar` (32 px chips, inverted selected, scrollable);
`Segmented`; `BottomTabs` (4 equal tabs, 2 px top indicator on active,
top hairline, `background` fill); `SearchBar` (flat glyph + muted
placeholder at list bottoms; filled `surface` box only where a box is
needed); outline/primary/danger/text/caps/icon buttons (44 px targets);
56 px inverted `FAB` bottom-right above tabs; `Field` (caption over
title value, muted placeholder); `SectionLabel`; borderless `Card`;
`SettingRow` with hairlines; inverted `Switch` (never Adwaita blue);
one-sentence `EmptyState`; 72×64 `surface` keypad keys; `surface`
dialogs/popovers/menus (48 px menu rows, focused entry gets a 1 px
`foreground` outline); outgoing inverted bubbles, incoming plain text,
10 px muted timestamps, caption day separators between hairlines.

`GlyphTile` (quick settings, 4-up at 360 px, 80 px tall, radius 14): a
20 px symbolic glyph over an 8-char caps label and a state line. Off is
the foreground ramp (3.5% fill, 10% hairline); on inverts; `working`
dashes the border at 40% while a toggle runs off-thread; `attention`
adds a 6 px foreground dot and lifts the state line to text weight.
`.xx-display-face` sets the brand display face (Space Mono) on clocks
while the UI font is a house mono.

## Never

Libadwaita blue, HeaderBar chrome, button gradients/shadows, default
GtkSwitch, bold headers, system-sans text, 8 px gutters, mixed
alignments, gray slabs behind tabs.
