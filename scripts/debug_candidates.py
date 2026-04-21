"""Debug script: print all notes around D6 candidates and check adjacency."""
from pathlib import Path
import xml.etree.ElementTree as ET

mxl = Path(r'e:\Project_Convert\build\天空之城_transposed_G.musicxml')
tree = ET.parse(str(mxl))
root = tree.getroot()
ns_raw = root.tag.split('}')[0]+'}' if '}' in root.tag else ''
ns = ns_raw


def local(tag):
    return tag.split('}')[-1] if '}' in tag else tag


def txt(e, default=''):
    return e.text if e is not None and e.text else default


# Collect all notes in voice 1
part = root.findall(f'{ns}part')[0]
divisions = 1
cumulative_tick = 0
last_start = 0

rows = []
for meas_idx, meas in enumerate(part.findall(f'{ns}measure')):
    for child in meas:
        loc = local(child.tag)
        if loc == 'attributes':
            d_e = child.find(f'{ns}divisions')
            if d_e is not None and d_e.text:
                divisions = int(d_e.text)
        elif loc == 'note':
            is_chord = child.find(f'{ns}chord') is not None
            is_rest  = child.find(f'{ns}rest') is not None
            dur_e = child.find(f'{ns}duration')
            dur = int(txt(dur_e, '0')) if dur_e is not None else 0
            if is_chord:
                note_tick = last_start
            else:
                note_tick = cumulative_tick
                last_start = cumulative_tick
                cumulative_tick += dur
            voice = txt(child.find(f'{ns}voice'), '1')
            pitch_e = child.find(f'{ns}pitch')
            if pitch_e is not None:
                step = txt(pitch_e.find(f'{ns}step'), '?')
                alt = float(txt(pitch_e.find(f'{ns}alter'), '0') or '0')
                oct_ = txt(pitch_e.find(f'{ns}octave'), '?')
                acc = 'b' if alt < 0 else ('#' if alt > 0 else '')
                pitch_str = step + acc + oct_
            else:
                pitch_str = 'REST'
            rows.append((meas_idx + 1, voice, pitch_str, note_tick, dur, is_rest, is_chord))
        elif loc == 'backup':
            d = child.find(f'{ns}duration')
            if d is not None and d.text:
                cumulative_tick -= int(d.text)
        elif loc == 'forward':
            d = child.find(f'{ns}duration')
            if d is not None and d.text:
                cumulative_tick += int(d.text)

# Show all voice-1 notes in measures 19-30 to see what's around D6 candidates
print("=== Voice 1 notes, measures 19-30 ===")
print(f"{'Meas':>5} {'Pitch':>8} {'Tick':>9} {'Dur':>7} {'EndTk':>9} {'Type':>7}")
for meas, voice, pitch, tick, dur, is_rest, is_chord in rows:
    if voice == '1' and 19 <= meas <= 30:
        kind = 'REST' if is_rest else ('CHORD' if is_chord else 'NOTE')
        print(f"{meas:>5} {pitch:>8} {tick:>9} {dur:>7} {tick+dur:>9} {kind:>7}")

print()
print("=== D6 candidates adjacency check ===")
# Group voice1 D6 notes by tick
d6_midi = 12 * (6 + 1) + [0,2,4,5,7,9,11].index(0)  # D6 = 74 midi... let's just filter by pitch
d6_notes = [(meas, tick, dur, is_rest, is_chord)
            for meas, voice, pitch, tick, dur, is_rest, is_chord in rows
            if voice == '1' and pitch in ('D6', 'D#6', 'Db6') and not is_rest and not is_chord]
d6_notes.sort(key=lambda x: x[1])

for i in range(len(d6_notes) - 1):
    a = d6_notes[i]
    b = d6_notes[i+1]
    adjacent = (a[1] + a[2] == b[1])  # a.end_tick == b.tick

    # Check if there are any rests in voice 1 between a.end_tick and b.tick
    rests_between = [(meas, tick, dur) for meas, voice, pitch, tick, dur, is_rest, is_chord in rows
                     if voice == '1' and is_rest and a[1] < tick < b[1]]

    # Check if there are other (non-D6, non-chord) notes in voice 1 BETWEEN them
    other_notes = [(meas, pitch, tick, dur) for meas, voice, pitch, tick, dur, is_rest, is_chord in rows
                   if voice == '1' and not is_rest and not is_chord
                   and a[1] < tick < b[1] and pitch not in ('D6', 'D#6', 'Db6')]

    print(f"D6@m{a[0]} tick={a[1]} end={a[1]+a[2]} → D6@m{b[0]} tick={b[1]}")
    print(f"  tick-adjacent={adjacent}  rests_between={rests_between}  other_notes={other_notes[:3]}")
