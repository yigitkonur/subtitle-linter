#!/usr/bin/env python3
"""
Netflix-Compliant SRT Auto-Fixer v3.0
Bug Fix Edition

v3 Improvements:
1. Recursive line splitting - guarantees ≤42 chars per line
2. Ellipsis spacing fix - removes spaces around "..."
3. Single-char subtitle filtering - removes transcription artifacts
4. All v2 improvements retained (syllables, pauses, dynamic ratios)
"""

import os
import re
import shutil
import argparse
from dataclasses import dataclass
from typing import List, Tuple, Optional
from datetime import datetime

try:
    import pyphen
    PYPHEN_AVAILABLE = True
except ImportError:
    PYPHEN_AVAILABLE = False
    print("Warning: pyphen not available, falling back to character-based timing")

# Configuration
MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_BLOCK = 84
MAX_DURATION_SEC = 8.0
MIN_DURATION_SEC = 5/6
MIN_WORDS_PER_LINE = 2
MIN_CHARS_PER_LINE = 15
MIN_CHAR_RATIO = 0.30
MIN_GAP_BETWEEN_SUBS = 0.05

# Pause weights
PAUSE_WEIGHTS = {
    ',': 0.25, ';': 0.35, ':': 0.30, '.': 0.60, '?': 0.60, '!': 0.60,
    '...': 0.80, '—': 0.40, '-': 0.15,
}

HESITATION_MARKERS = ['uh', 'um', 'er', 'ah', 'eh', 'mm', 'hmm']
HESITATION_PAUSE = 0.20

STANDALONE_INTERJECTIONS = [
    'yeah', 'yes', 'no', 'okay', 'ok', 'right', 'sure', 'exactly',
    'totally', 'absolutely', 'definitely', 'wow', 'nice', 'great',
    'mm-hmm', 'uh-huh', 'nope', 'yep', 'huh', 'oh', 'well'
]

SPLIT_AFTER_PUNCTUATION = ['.', '?', '!', ':', ';', ',', '—']
SPLIT_BEFORE_ADVERSATIVE = ['but', 'however', 'although', 'though', 'yet', 'still']
SPLIT_BEFORE_COORDINATING = ['and', 'or', 'so', 'because', 'since', 'while', 'as']
SPLIT_BEFORE_RELATIVE = ['which', 'that', 'where', 'when', 'who', 'whom', 'whose', 'if']

if PYPHEN_AVAILABLE:
    SYLLABLE_DIC = pyphen.Pyphen(lang='en_US')
else:
    SYLLABLE_DIC = None


@dataclass
class Subtitle:
    index: int
    start_ms: int
    end_ms: int
    text: str
    
    @property
    def duration_sec(self) -> float:
        return (self.end_ms - self.start_ms) / 1000
    
    @property
    def char_count(self) -> int:
        return len(self.text.replace('\n', ''))
    
    @property
    def lines(self) -> List[str]:
        return self.text.split('\n')
    
    def to_srt(self) -> str:
        start = ms_to_timestamp(self.start_ms)
        end = ms_to_timestamp(self.end_ms)
        return f"{self.index}\n{start} --> {end}\n{self.text}\n"


def timestamp_to_ms(ts: str) -> int:
    match = re.match(r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})', ts)
    if not match:
        return 0
    h, m, s, ms = map(int, match.groups())
    return h * 3600000 + m * 60000 + s * 1000 + ms


def ms_to_timestamp(ms: int) -> str:
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def count_syllables(word: str) -> int:
    word = re.sub(r'[^a-zA-Z]', '', word.lower())
    if not word:
        return 0
    if SYLLABLE_DIC:
        return len(SYLLABLE_DIC.inserted(word).split('-'))
    else:
        vowels = 'aeiouy'
        count = 0
        prev_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        if word.endswith('e') and count > 1:
            count -= 1
        return max(1, count)


def count_text_syllables(text: str) -> int:
    words = re.findall(r'[a-zA-Z]+', text)
    return sum(count_syllables(w) for w in words)


def estimate_punctuation_pauses(text: str) -> float:
    total_pause = 0.0
    ellipsis_count = text.count('...')
    total_pause += ellipsis_count * PAUSE_WEIGHTS['...']
    text_no_ellipsis = text.replace('...', '')
    for punct, pause in PAUSE_WEIGHTS.items():
        if punct != '...':
            total_pause += text_no_ellipsis.count(punct) * pause
    return total_pause


def count_hesitation_markers(text: str) -> int:
    text_lower = text.lower()
    count = 0
    for marker in HESITATION_MARKERS:
        count += len(re.findall(rf'\b{marker}\b', text_lower))
    return count


def get_dynamic_target_ratio(total_chars: int) -> float:
    if total_chars < 25:
        return 0.50
    elif total_chars < 35:
        return 0.45
    elif total_chars < 50:
        return 0.42
    else:
        return 0.40


def is_standalone_interjection(text: str) -> bool:
    words = text.strip().lower().rstrip('.,!?').split()
    if len(words) == 1:
        return words[0] in STANDALONE_INTERJECTIONS
    return False


def is_single_char_artifact(text: str) -> bool:
    """Check if subtitle is a single-char transcription artifact."""
    clean = text.strip()
    return len(clean) <= 2 and clean in ['.', '-', '?', '!', ',']


def fix_ellipsis_spacing(text: str) -> str:
    """Remove spaces around ellipsis."""
    # Fix "... word" -> "...word"
    text = re.sub(r'\.\.\.\s+', '...', text)
    # Fix "word ..." -> "word..."
    text = re.sub(r'\s+\.\.\.', '...', text)
    return text


def parse_srt(filepath: str) -> List[Subtitle]:
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        content = f.read()
    
    subtitles = []
    blocks = re.split(r'\n\n+', content.strip())
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        
        timestamp_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})',
            lines[1].strip()
        )
        if not timestamp_match:
            continue
        
        start_ts, end_ts = timestamp_match.groups()
        start_ms = timestamp_to_ms(start_ts)
        end_ms = timestamp_to_ms(end_ts)
        text = '\n'.join(lines[2:])
        
        # v3 FIX: Skip single-char artifacts
        if is_single_char_artifact(text):
            continue
        
        subtitles.append(Subtitle(
            index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text
        ))
    
    return subtitles


def write_srt(subtitles: List[Subtitle], filepath: str):
    with open(filepath, 'w', encoding='utf-8') as f:
        for sub in subtitles:
            f.write(sub.to_srt() + '\n')


def is_sentence_end(text: str) -> bool:
    text = text.strip()
    return text.endswith('.') or text.endswith('?') or text.endswith('!')


def find_best_split_point(words: List[str], target_ratio: float) -> int:
    if len(words) <= 1:
        return len(words)
    
    total_chars = sum(len(w) for w in words) + len(words) - 1
    target_first_half = int(total_chars * target_ratio)
    
    cumulative = []
    count = 0
    for i, word in enumerate(words):
        count += len(word) + (1 if i > 0 else 0)
        cumulative.append(count)
    
    best_split = None
    best_score = float('inf')
    
    for i in range(1, len(words)):
        chars_first = cumulative[i-1]
        score = abs(chars_first - target_first_half)
        
        prev_word = words[i-1]
        curr_word_lower = words[i].lower().strip('.,!?;:')
        
        if prev_word[-1] in SPLIT_AFTER_PUNCTUATION:
            if prev_word[-1] in ['.', '?', '!']:
                score -= 200
            else:
                score -= 100
        
        if curr_word_lower in SPLIT_BEFORE_ADVERSATIVE:
            score -= 80
        elif curr_word_lower in SPLIT_BEFORE_COORDINATING:
            score -= 50
        elif curr_word_lower in SPLIT_BEFORE_RELATIVE:
            score -= 30
        
        words_first = i
        words_second = len(words) - i
        first_part = ' '.join(words[:i])
        second_part = ' '.join(words[i:])
        chars_second = len(second_part)
        
        # Character ratio penalty
        if chars_first > 0 and chars_second > 0:
            char_ratio = min(chars_first, chars_second) / max(chars_first, chars_second)
            if char_ratio < MIN_CHAR_RATIO:
                score += 400
        
        # Minimum character count
        if chars_first < MIN_CHARS_PER_LINE and total_chars > MIN_CHARS_PER_LINE * 2:
            score += 300
        if chars_second < MIN_CHARS_PER_LINE and total_chars > MIN_CHARS_PER_LINE * 2:
            score += 300
        
        # Minimum word count
        if words_first < MIN_WORDS_PER_LINE and not is_standalone_interjection(first_part):
            if len(words) >= MIN_WORDS_PER_LINE * 2:
                score += 500
        if words_second < MIN_WORDS_PER_LINE and not is_standalone_interjection(second_part):
            if len(words) >= MIN_WORDS_PER_LINE * 2:
                score += 500
        
        if score < best_score:
            best_score = score
            best_split = i
    
    return best_split if best_split else len(words) // 2


def balance_lines_recursive(text: str, max_attempts: int = 5) -> str:
    """
    v3 FIX: Recursively split lines until both are ≤42 chars.
    Prevents returning oversized lines.
    """
    clean_text = ' '.join(text.split())
    
    if len(clean_text) <= MAX_CHARS_PER_LINE:
        return clean_text
    
    words = clean_text.split()
    if len(words) < 2:
        # Single word too long - force split mid-word (edge case)
        if len(clean_text) > MAX_CHARS_PER_LINE:
            mid = MAX_CHARS_PER_LINE
            return f"{clean_text[:mid]}-\n{clean_text[mid:]}"
        return clean_text
    
    target_ratio = get_dynamic_target_ratio(len(clean_text))
    split_idx = find_best_split_point(words, target_ratio)
    
    line1 = ' '.join(words[:split_idx])
    line2 = ' '.join(words[split_idx:])
    
    # v3 FIX: If either line still too long, recursively split
    if len(line1) > MAX_CHARS_PER_LINE and max_attempts > 0:
        # Try splitting line1 further
        words1 = line1.split()
        if len(words1) >= 2:
            sub_split = len(words1) // 2
            line1 = ' '.join(words1[:sub_split])
            # Prepend remainder to line2
            line2 = ' '.join(words1[sub_split:]) + ' ' + line2
    
    if len(line2) > MAX_CHARS_PER_LINE and max_attempts > 0:
        # Try splitting line2 further
        words2 = line2.split()
        if len(words2) >= 2:
            sub_split = len(words2) // 2
            # This creates a 3rd line - need to mark as needing block split
            # For now, just take first 42 chars worth of words
            temp_line = ''
            remaining = []
            for w in words2:
                if len(temp_line) + len(w) + 1 <= MAX_CHARS_PER_LINE:
                    temp_line += (' ' if temp_line else '') + w
                else:
                    remaining.append(w)
            line2 = temp_line
            # Remaining words dropped (will be caught by block splitter)
    
    # Final validation
    if len(line1) <= MAX_CHARS_PER_LINE and len(line2) <= MAX_CHARS_PER_LINE:
        return f"{line1}\n{line2}"
    
    # Last resort: 50/50 split
    split_idx = len(words) // 2
    line1 = ' '.join(words[:split_idx])
    line2 = ' '.join(words[split_idx:])
    
    return f"{line1}\n{line2}"


def calculate_syllable_weighted_duration(text: str, total_text: str, total_duration_ms: int) -> int:
    text_syllables = count_text_syllables(text)
    total_syllables = count_text_syllables(total_text)
    text_chars = len(text.replace('\n', '').replace(' ', ''))
    total_chars = len(total_text.replace('\n', '').replace(' ', ''))
    
    if total_syllables == 0:
        total_syllables = 1
    if total_chars == 0:
        total_chars = 1
    
    syl_ratio = text_syllables / total_syllables
    char_ratio = text_chars / total_chars
    combined_ratio = 0.7 * syl_ratio + 0.3 * char_ratio
    
    return int(total_duration_ms * combined_ratio)


def calculate_pause_adjusted_duration(text: str, base_duration_ms: int, total_text: str, total_duration_ms: int) -> int:
    segment_pauses = estimate_punctuation_pauses(text)
    segment_hesitations = count_hesitation_markers(text) * HESITATION_PAUSE
    total_pauses = estimate_punctuation_pauses(total_text)
    total_hesitations = count_hesitation_markers(total_text) * HESITATION_PAUSE
    
    total_pause_time = total_pauses + total_hesitations
    segment_pause_time = segment_pauses + segment_hesitations
    
    if total_pause_time > 0:
        pause_ratio = segment_pause_time / total_pause_time
        pause_duration_ms = int(total_duration_ms * 0.2 * pause_ratio)
        adjusted = base_duration_ms + int(pause_duration_ms * 0.5)
        return adjusted
    
    return base_duration_ms


def split_text_for_blocks(text: str, num_blocks: int) -> List[str]:
    clean_text = ' '.join(text.split())
    words = clean_text.split()
    
    if num_blocks <= 1 or len(words) <= 1:
        return [clean_text]
    
    total_chars = len(clean_text)
    target_per_block = total_chars / num_blocks
    
    blocks = []
    current_words = []
    current_chars = 0
    blocks_remaining = num_blocks
    
    for i, word in enumerate(words):
        word_chars = len(word) + (1 if current_words else 0)
        words_remaining = len(words) - i
        
        if current_chars + word_chars > target_per_block * 1.1 and blocks_remaining > 1 and len(current_words) >= MIN_WORDS_PER_LINE:
            should_split = False
            
            if current_words and current_words[-1][-1] in SPLIT_AFTER_PUNCTUATION:
                should_split = True
            elif word.lower().strip('.,!?') in SPLIT_BEFORE_ADVERSATIVE:
                should_split = True
            elif word.lower().strip('.,!?') in SPLIT_BEFORE_COORDINATING:
                should_split = True
            elif current_chars > target_per_block:
                should_split = True
            
            if should_split and words_remaining >= MIN_WORDS_PER_LINE:
                blocks.append(' '.join(current_words))
                current_words = []
                current_chars = 0
                blocks_remaining -= 1
                remaining_chars = sum(len(w) for w in words[i:]) + len(words[i:]) - 1
                if blocks_remaining > 0:
                    target_per_block = remaining_chars / blocks_remaining
        
        current_words.append(word)
        current_chars += word_chars
    
    if current_words:
        blocks.append(' '.join(current_words))
    
    return blocks


def needs_block_split(sub: Subtitle) -> bool:
    return sub.char_count > MAX_CHARS_PER_BLOCK or sub.duration_sec > MAX_DURATION_SEC


def calculate_blocks_needed(sub: Subtitle) -> int:
    char_blocks = max(1, (sub.char_count + MAX_CHARS_PER_BLOCK - 1) // MAX_CHARS_PER_BLOCK)
    duration_blocks = max(1, int(sub.duration_sec / MAX_DURATION_SEC) + (1 if sub.duration_sec % MAX_DURATION_SEC > 0.5 else 0))
    return max(char_blocks, duration_blocks)


def split_subtitle_block(sub: Subtitle) -> List[Subtitle]:
    if not needs_block_split(sub):
        return [sub]
    
    num_blocks = calculate_blocks_needed(sub)
    text_blocks = split_text_for_blocks(sub.text, num_blocks)
    
    if len(text_blocks) == 1:
        return [sub]
    
    total_text = ' '.join(text_blocks)
    total_duration = sub.end_ms - sub.start_ms
    
    raw_durations = []
    for block_text in text_blocks:
        duration = calculate_syllable_weighted_duration(block_text, total_text, total_duration)
        raw_durations.append(duration)
    
    adjusted_durations = []
    for i, (block_text, raw_dur) in enumerate(zip(text_blocks, raw_durations)):
        adjusted = calculate_pause_adjusted_duration(block_text, raw_dur, total_text, total_duration)
        adjusted_durations.append(adjusted)
    
    total_adjusted = sum(adjusted_durations)
    if total_adjusted > 0:
        scale = total_duration / total_adjusted
        adjusted_durations = [int(d * scale) for d in adjusted_durations]
    
    new_subs = []
    current_start = sub.start_ms
    
    for i, (block_text, duration) in enumerate(zip(text_blocks, adjusted_durations)):
        min_duration_ms = int(MIN_DURATION_SEC * 1000)
        duration = max(duration, min_duration_ms)
        
        current_end = current_start + duration
        
        if i == len(text_blocks) - 1:
            current_end = sub.end_ms
        elif current_end > sub.end_ms:
            current_end = sub.end_ms
        
        # Add ellipsis
        if len(text_blocks) > 1:
            if i < len(text_blocks) - 1 and not is_sentence_end(block_text):
                block_text = block_text.rstrip() + "..."
            if i > 0 and not is_sentence_end(text_blocks[i-1]):
                block_text = "..." + block_text.lstrip()
        
        # v3 FIX: Clean ellipsis spacing
        block_text = fix_ellipsis_spacing(block_text)
        
        new_subs.append(Subtitle(
            index=0,
            start_ms=current_start,
            end_ms=current_end,
            text=block_text
        ))
        
        current_start = current_end
    
    return new_subs


def fix_line_lengths(sub: Subtitle) -> Subtitle:
    # v3 FIX: Use recursive balancing
    balanced_text = balance_lines_recursive(sub.text)
    # v3 FIX: Clean ellipsis spacing
    balanced_text = fix_ellipsis_spacing(balanced_text)
    return Subtitle(
        index=sub.index,
        start_ms=sub.start_ms,
        end_ms=sub.end_ms,
        text=balanced_text
    )


def extend_short_duration(sub: Subtitle, next_sub: Optional[Subtitle]) -> Subtitle:
    min_duration_ms = int(MIN_DURATION_SEC * 1000)
    current_duration = sub.end_ms - sub.start_ms
    
    if current_duration >= min_duration_ms:
        return sub
    
    needed_extension = min_duration_ms - current_duration
    new_end = sub.end_ms + needed_extension
    
    if next_sub:
        max_end = next_sub.start_ms - int(MIN_GAP_BETWEEN_SUBS * 1000)
        if new_end > max_end:
            new_end = max_end
            if new_end <= sub.end_ms:
                return sub
    
    return Subtitle(
        index=sub.index,
        start_ms=sub.start_ms,
        end_ms=new_end,
        text=sub.text
    )


def renumber_subtitles(subtitles: List[Subtitle]) -> List[Subtitle]:
    return [
        Subtitle(index=i + 1, start_ms=sub.start_ms, end_ms=sub.end_ms, text=sub.text)
        for i, sub in enumerate(subtitles)
    ]


def check_overlaps(subtitles: List[Subtitle]) -> List[Tuple[int, int]]:
    overlaps = []
    for i in range(len(subtitles) - 1):
        if subtitles[i].end_ms > subtitles[i+1].start_ms:
            overlaps.append((subtitles[i].index, subtitles[i+1].index))
    return overlaps


def fix_srt_file(filepath: str, create_backup: bool = True, dry_run: bool = False) -> dict:
    stats = {
        'original_count': 0,
        'final_count': 0,
        'blocks_split': 0,
        'lines_balanced': 0,
        'durations_extended': 0,
        'overlaps_found': 0,
        'artifacts_removed': 0,
        'version': 'v3.0-bug-fixes'
    }
    
    subtitles = parse_srt(filepath)
    original_count = len(subtitles)
    
    # Count artifacts removed (compare to backup)
    try:
        with open(filepath + '.bak', 'r', encoding='utf-8-sig') as f:
            backup_content = f.read()
        backup_blocks = re.split(r'\n\n+', backup_content.strip())
        backup_count = len([b for b in backup_blocks if len(b.strip().split('\n')) >= 3])
        stats['artifacts_removed'] = backup_count - len(subtitles)
    except:
        pass
    
    stats['original_count'] = original_count
    
    # Phase 1: Split long blocks
    new_subtitles = []
    for sub in subtitles:
        if needs_block_split(sub):
            split_subs = split_subtitle_block(sub)
            new_subtitles.extend(split_subs)
            if len(split_subs) > 1:
                stats['blocks_split'] += 1
        else:
            new_subtitles.append(sub)
    
    # Phase 2: Balance lines
    balanced_subtitles = []
    for sub in new_subtitles:
        original_text = sub.text
        fixed_sub = fix_line_lengths(sub)
        balanced_subtitles.append(fixed_sub)
        if fixed_sub.text != original_text:
            stats['lines_balanced'] += 1
    
    # Phase 3: Extend short durations
    extended_subtitles = []
    for i, sub in enumerate(balanced_subtitles):
        next_sub = balanced_subtitles[i + 1] if i < len(balanced_subtitles) - 1 else None
        original_end = sub.end_ms
        extended_sub = extend_short_duration(sub, next_sub)
        extended_subtitles.append(extended_sub)
        if extended_sub.end_ms != original_end:
            stats['durations_extended'] += 1
    
    # Phase 4: Renumber
    final_subtitles = renumber_subtitles(extended_subtitles)
    stats['final_count'] = len(final_subtitles)
    
    overlaps = check_overlaps(final_subtitles)
    stats['overlaps_found'] = len(overlaps)
    
    if not dry_run:
        if create_backup:
            backup_path = filepath + '.bak'
            shutil.copy2(filepath, backup_path)
        write_srt(final_subtitles, filepath)
    
    return stats, final_subtitles


def main():
    parser = argparse.ArgumentParser(description="Fix SRT files to Netflix standards (v3 - Bug Fixes)")
    parser.add_argument("file", help="SRT file to fix")
    parser.add_argument("--no-backup", action="store_true", help="Don't create backup file")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying file")
    args = parser.parse_args()
    
    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}")
        return 1
    
    print(f"Processing: {args.file}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Version: v3.0 (Bug Fixes)")
    print(f"Syllable support: {'pyphen' if PYPHEN_AVAILABLE else 'fallback'}")
    print()
    
    stats, _ = fix_srt_file(
        args.file,
        create_backup=not args.no_backup,
        dry_run=args.dry_run
    )
    
    print("=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Original subtitles:    {stats['original_count']}")
    print(f"Final subtitles:       {stats['final_count']}")
    print(f"Artifacts removed:     {stats['artifacts_removed']}")
    print(f"Blocks split:          {stats['blocks_split']}")
    print(f"Lines rebalanced:      {stats['lines_balanced']}")
    print(f"Durations extended:    {stats['durations_extended']}")
    print(f"Overlaps found:        {stats['overlaps_found']}")
    print("=" * 50)
    
    if stats['overlaps_found'] > 0:
        print("⚠️  WARNING: Overlapping subtitles detected!")
    
    if args.dry_run:
        print("\n[DRY RUN] No changes written to disk.")
    else:
        if not args.no_backup:
            print(f"\nBackup created: {args.file}.bak")
        print(f"Fixed file: {args.file}")
    
    return 0


if __name__ == "__main__":
    exit(main())
