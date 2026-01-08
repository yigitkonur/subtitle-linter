#!/usr/bin/env python3
"""
Netflix Subtitle Standards Validator
Validates SRT files against Netflix Timed Text Style Guide rules.

Testable Rules:
1. Character limit: 42 chars per line (Latin alphabet)
2. Line limit: 2 lines max per subtitle
3. Duration: Min 0.833s (5/6 sec), Max 7s
4. Reading speed: Max 17 CPS (adult), 15 CPS (children)
5. Line balance: Avoid extreme imbalance (e.g., 40 chars + 2 chars)
6. No double spaces
7. Dual speaker format (hyphen at line start)
"""

import os
import re
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from collections import defaultdict

# Netflix standards
MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MIN_DURATION_SEC = 5/6  # 0.833 seconds
MAX_DURATION_SEC = 7.0
MAX_CPS_ADULT = 17
MAX_CPS_CHILDREN = 15
LINE_BALANCE_THRESHOLD = 0.25  # Shorter line should be at least 25% of longer


@dataclass
class Subtitle:
    """Represents a single subtitle entry."""
    index: int
    start_time: str
    end_time: str
    text: str
    line_number: int  # Line number in file where subtitle starts
    
    @property
    def start_seconds(self) -> float:
        return self._parse_time(self.start_time)
    
    @property
    def end_seconds(self) -> float:
        return self._parse_time(self.end_time)
    
    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds
    
    @property
    def char_count(self) -> int:
        return len(self.text.replace('\n', ''))
    
    @property
    def cps(self) -> float:
        """Characters per second."""
        if self.duration <= 0:
            return float('inf')
        return self.char_count / self.duration
    
    @property
    def lines(self) -> List[str]:
        return self.text.split('\n')
    
    @property
    def line_count(self) -> int:
        return len(self.lines)
    
    def _parse_time(self, time_str: str) -> float:
        """Parse SRT timestamp to seconds."""
        # Format: HH:MM:SS,mmm
        match = re.match(r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})', time_str)
        if not match:
            return 0.0
        h, m, s, ms = map(int, match.groups())
        return h * 3600 + m * 60 + s + ms / 1000


@dataclass
class Violation:
    """Represents a rule violation."""
    rule: str
    subtitle_index: int
    line_number: int
    message: str
    severity: str  # 'error', 'warning'
    auto_fixable: bool
    fix_suggestion: Optional[str] = None
    current_value: Optional[str] = None
    limit_value: Optional[str] = None


@dataclass
class FileReport:
    """Report for a single file."""
    filepath: str
    total_subtitles: int
    violations: List[Violation] = field(default_factory=list)
    
    @property
    def violation_count(self) -> int:
        return len(self.violations)
    
    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == 'error')
    
    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == 'warning')
    
    @property
    def auto_fixable_count(self) -> int:
        return sum(1 for v in self.violations if v.auto_fixable)


def parse_srt(filepath: str) -> List[Subtitle]:
    """Parse an SRT file into Subtitle objects."""
    subtitles = []
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        content = f.read()
    
    # Split by double newlines (subtitle blocks)
    blocks = re.split(r'\n\n+', content.strip())
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        # Parse index
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        
        # Parse timestamp line
        timestamp_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})',
            lines[1].strip()
        )
        if not timestamp_match:
            continue
        
        start_time, end_time = timestamp_match.groups()
        
        # Join remaining lines as text
        text = '\n'.join(lines[2:])
        
        # Calculate approximate line number (rough estimate)
        line_number = content[:content.find(block)].count('\n') + 1
        
        subtitles.append(Subtitle(
            index=index,
            start_time=start_time,
            end_time=end_time,
            text=text,
            line_number=line_number
        ))
    
    return subtitles


def check_line_length(subtitle: Subtitle) -> List[Violation]:
    """Check if any line exceeds 42 characters."""
    violations = []
    
    for i, line in enumerate(subtitle.lines):
        line_len = len(line)
        if line_len > MAX_CHARS_PER_LINE:
            # Calculate how to split this line
            words = line.split()
            if len(words) > 1:
                # Can be auto-fixed by splitting into multiple lines
                mid = len(words) // 2
                suggested_line1 = ' '.join(words[:mid])
                suggested_line2 = ' '.join(words[mid:])
                fix = f"Split: \"{suggested_line1}\" / \"{suggested_line2}\""
                auto_fixable = True
            else:
                fix = "Single word too long - manual review needed"
                auto_fixable = False
            
            violations.append(Violation(
                rule="LINE_LENGTH",
                subtitle_index=subtitle.index,
                line_number=subtitle.line_number,
                message=f"Line {i+1} has {line_len} chars (max {MAX_CHARS_PER_LINE})",
                severity="error",
                auto_fixable=auto_fixable,
                fix_suggestion=fix,
                current_value=str(line_len),
                limit_value=str(MAX_CHARS_PER_LINE)
            ))
    
    return violations


def check_line_count(subtitle: Subtitle) -> List[Violation]:
    """Check if subtitle has more than 2 lines."""
    violations = []
    
    if subtitle.line_count > MAX_LINES:
        violations.append(Violation(
            rule="LINE_COUNT",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message=f"Subtitle has {subtitle.line_count} lines (max {MAX_LINES})",
            severity="error",
            auto_fixable=False,  # Requires timestamp splitting
            fix_suggestion="Split into multiple subtitles with adjusted timestamps",
            current_value=str(subtitle.line_count),
            limit_value=str(MAX_LINES)
        ))
    
    return violations


def check_duration(subtitle: Subtitle) -> List[Violation]:
    """Check subtitle duration limits."""
    violations = []
    duration = subtitle.duration
    
    if duration < MIN_DURATION_SEC:
        violations.append(Violation(
            rule="MIN_DURATION",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message=f"Duration {duration:.2f}s is too short (min {MIN_DURATION_SEC:.2f}s)",
            severity="warning",
            auto_fixable=False,
            fix_suggestion="Extend subtitle duration or merge with adjacent",
            current_value=f"{duration:.2f}s",
            limit_value=f"{MIN_DURATION_SEC:.2f}s"
        ))
    
    if duration > MAX_DURATION_SEC:
        violations.append(Violation(
            rule="MAX_DURATION",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message=f"Duration {duration:.2f}s is too long (max {MAX_DURATION_SEC}s)",
            severity="warning",
            auto_fixable=False,
            fix_suggestion="Split into multiple subtitles",
            current_value=f"{duration:.2f}s",
            limit_value=f"{MAX_DURATION_SEC}s"
        ))
    
    return violations


def check_reading_speed(subtitle: Subtitle, max_cps: int = MAX_CPS_ADULT) -> List[Violation]:
    """Check reading speed (characters per second)."""
    violations = []
    cps = subtitle.cps
    
    if cps > max_cps:
        violations.append(Violation(
            rule="READING_SPEED",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message=f"Reading speed {cps:.1f} CPS exceeds limit ({max_cps} CPS)",
            severity="warning",
            auto_fixable=False,
            fix_suggestion="Shorten text or extend duration",
            current_value=f"{cps:.1f} CPS",
            limit_value=f"{max_cps} CPS"
        ))
    
    return violations


def check_line_balance(subtitle: Subtitle) -> List[Violation]:
    """Check if line lengths are reasonably balanced."""
    violations = []
    
    if subtitle.line_count != 2:
        return violations
    
    lines = subtitle.lines
    len1, len2 = len(lines[0]), len(lines[1])
    
    if len1 == 0 or len2 == 0:
        return violations
    
    # Check balance ratio
    shorter = min(len1, len2)
    longer = max(len1, len2)
    ratio = shorter / longer
    
    if ratio < LINE_BALANCE_THRESHOLD:
        # Calculate better split
        all_text = ' '.join(lines)
        words = all_text.split()
        if len(words) >= 2:
            mid = len(words) // 2
            balanced_l1 = ' '.join(words[:mid])
            balanced_l2 = ' '.join(words[mid:])
            fix = f"Rebalance: \"{balanced_l1}\" / \"{balanced_l2}\""
            auto_fixable = True
        else:
            fix = "Cannot rebalance single word"
            auto_fixable = False
        
        violations.append(Violation(
            rule="LINE_BALANCE",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message=f"Unbalanced lines: {len1} chars vs {len2} chars (ratio {ratio:.2f})",
            severity="warning",
            auto_fixable=auto_fixable,
            fix_suggestion=fix,
            current_value=f"{len1}/{len2} chars",
            limit_value=f"ratio >= {LINE_BALANCE_THRESHOLD}"
        ))
    
    return violations


def check_double_spaces(subtitle: Subtitle) -> List[Violation]:
    """Check for double spaces in text."""
    violations = []
    
    if '  ' in subtitle.text:
        violations.append(Violation(
            rule="DOUBLE_SPACE",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message="Contains double spaces",
            severity="warning",
            auto_fixable=True,
            fix_suggestion="Replace double spaces with single space"
        ))
    
    return violations


def check_dual_speaker_format(subtitle: Subtitle) -> List[Violation]:
    """Check dual speaker subtitle format."""
    violations = []
    lines = subtitle.lines
    
    # Check if this looks like a dual speaker subtitle
    hyphen_lines = [l for l in lines if l.strip().startswith('-')]
    
    if len(hyphen_lines) == 1 and len(lines) > 1:
        # Inconsistent - one line has hyphen, others don't
        violations.append(Violation(
            rule="DUAL_SPEAKER_FORMAT",
            subtitle_index=subtitle.index,
            line_number=subtitle.line_number,
            message="Inconsistent dual speaker format (only some lines have hyphens)",
            severity="warning",
            auto_fixable=False,
            fix_suggestion="Ensure all speaker lines start with hyphen or none do"
        ))
    
    # Check for space after hyphen (should be no space)
    for i, line in enumerate(lines):
        if line.strip().startswith('- '):
            violations.append(Violation(
                rule="DUAL_SPEAKER_FORMAT",
                subtitle_index=subtitle.index,
                line_number=subtitle.line_number,
                message=f"Line {i+1}: Space after hyphen in dual speaker subtitle",
                severity="warning",
                auto_fixable=True,
                fix_suggestion="Remove space after hyphen"
            ))
    
    return violations


def validate_file(filepath: str, max_cps: int = MAX_CPS_ADULT) -> FileReport:
    """Validate a single SRT file against Netflix rules."""
    subtitles = parse_srt(filepath)
    report = FileReport(filepath=filepath, total_subtitles=len(subtitles))
    
    for subtitle in subtitles:
        # Run all checks
        report.violations.extend(check_line_length(subtitle))
        report.violations.extend(check_line_count(subtitle))
        report.violations.extend(check_duration(subtitle))
        report.violations.extend(check_reading_speed(subtitle, max_cps))
        report.violations.extend(check_line_balance(subtitle))
        report.violations.extend(check_double_spaces(subtitle))
        report.violations.extend(check_dual_speaker_format(subtitle))
    
    return report


def find_en_srt_files(pairs_dir: str) -> List[str]:
    """Find all English SRT files in pairs directory."""
    files = []
    for root, dirs, filenames in os.walk(pairs_dir):
        for filename in filenames:
            if filename.startswith('en_') and filename.endswith('.srt'):
                files.append(os.path.join(root, filename))
    return sorted(files)


def generate_summary(reports: List[FileReport]) -> Dict:
    """Generate summary statistics from all reports."""
    summary = {
        "total_files": len(reports),
        "total_subtitles": sum(r.total_subtitles for r in reports),
        "total_violations": sum(r.violation_count for r in reports),
        "files_with_violations": sum(1 for r in reports if r.violation_count > 0),
        "files_clean": sum(1 for r in reports if r.violation_count == 0),
        "total_errors": sum(r.error_count for r in reports),
        "total_warnings": sum(r.warning_count for r in reports),
        "total_auto_fixable": sum(r.auto_fixable_count for r in reports),
        "violations_by_rule": defaultdict(lambda: {"count": 0, "auto_fixable": 0, "files": set()}),
    }
    
    for report in reports:
        for v in report.violations:
            summary["violations_by_rule"][v.rule]["count"] += 1
            if v.auto_fixable:
                summary["violations_by_rule"][v.rule]["auto_fixable"] += 1
            summary["violations_by_rule"][v.rule]["files"].add(report.filepath)
    
    # Convert sets to counts for JSON serialization
    for rule, data in summary["violations_by_rule"].items():
        data["affected_files"] = len(data["files"])
        del data["files"]
    
    summary["violations_by_rule"] = dict(summary["violations_by_rule"])
    
    return summary


def generate_markdown_report(reports: List[FileReport], summary: Dict, output_path: str):
    """Generate a detailed Markdown report."""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Netflix Subtitle Standards Validation Report\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # Executive Summary
        f.write("## Executive Summary\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Total Files Analyzed | {summary['total_files']} |\n")
        f.write(f"| Total Subtitles | {summary['total_subtitles']} |\n")
        f.write(f"| Files with Violations | {summary['files_with_violations']} ({summary['files_with_violations']/summary['total_files']*100:.1f}%) |\n")
        f.write(f"| Clean Files | {summary['files_clean']} |\n")
        f.write(f"| Total Violations | {summary['total_violations']} |\n")
        f.write(f"| Errors (Critical) | {summary['total_errors']} |\n")
        f.write(f"| Warnings | {summary['total_warnings']} |\n")
        f.write(f"| **Auto-Fixable** | **{summary['total_auto_fixable']}** ({summary['total_auto_fixable']/max(summary['total_violations'],1)*100:.1f}%) |\n")
        f.write(f"| Manual Review Needed | {summary['total_violations'] - summary['total_auto_fixable']} |\n\n")
        
        # Violations by Rule
        f.write("## Violations by Rule\n\n")
        f.write("| Rule | Count | Auto-Fixable | Manual | Affected Files |\n")
        f.write("|------|-------|--------------|--------|----------------|\n")
        
        for rule, data in sorted(summary["violations_by_rule"].items(), key=lambda x: -x[1]["count"]):
            manual = data["count"] - data["auto_fixable"]
            f.write(f"| {rule} | {data['count']} | {data['auto_fixable']} | {manual} | {data['affected_files']} |\n")
        
        f.write("\n")
        
        # Rule Descriptions
        f.write("## Rule Descriptions & Fix Strategies\n\n")
        
        rule_info = {
            "LINE_LENGTH": {
                "desc": "Lines exceed 42 characters",
                "fix": "**Auto-fixable:** Split line at word boundaries to create 2-line subtitle",
                "netflix_ref": "Section 5: 42 characters per line for Latin alphabet"
            },
            "LINE_COUNT": {
                "desc": "Subtitle has more than 2 lines",
                "fix": "**Manual:** Requires splitting into multiple subtitles with new timestamps",
                "netflix_ref": "Section 17: 2 lines maximum"
            },
            "MIN_DURATION": {
                "desc": "Subtitle duration less than 5/6 second (0.833s)",
                "fix": "**Manual:** Extend duration or merge with adjacent subtitle",
                "netflix_ref": "Section 12: Minimum 5/6 second per event"
            },
            "MAX_DURATION": {
                "desc": "Subtitle duration exceeds 7 seconds",
                "fix": "**Manual:** Split into multiple subtitles with adjusted timestamps",
                "netflix_ref": "Section 12: Maximum 7 seconds per event"
            },
            "READING_SPEED": {
                "desc": "Characters per second exceeds 17 CPS (adult content)",
                "fix": "**Manual:** Shorten text (edit/condense) or extend duration",
                "netflix_ref": "Section 23: Up to 17 CPS for adult programs"
            },
            "LINE_BALANCE": {
                "desc": "Two-line subtitle has severely unbalanced lines (e.g., 40+2 chars)",
                "fix": "**Auto-fixable:** Redistribute words between lines for bottom-heavy layout",
                "netflix_ref": "Section 17: Favor bottom-heavy two-line subtitles"
            },
            "DOUBLE_SPACE": {
                "desc": "Text contains double spaces",
                "fix": "**Auto-fixable:** Replace with single space",
                "netflix_ref": "Section 22: Double spaces are not permitted"
            },
            "DUAL_SPEAKER_FORMAT": {
                "desc": "Dual speaker subtitle has formatting issues",
                "fix": "**Partial auto-fix:** Remove space after hyphen; manual review for structure",
                "netflix_ref": "Section 11: Hyphen without space for dual speakers"
            },
        }
        
        for rule, info in rule_info.items():
            if rule in summary["violations_by_rule"]:
                f.write(f"### {rule}\n\n")
                f.write(f"**Description:** {info['desc']}\n\n")
                f.write(f"**Netflix Reference:** {info['netflix_ref']}\n\n")
                f.write(f"**Fix Strategy:** {info['fix']}\n\n")
                f.write(f"**Occurrences:** {summary['violations_by_rule'][rule]['count']}\n\n")
        
        # Top 20 Worst Files
        f.write("## Top 20 Files by Violation Count\n\n")
        sorted_reports = sorted(reports, key=lambda r: -r.violation_count)[:20]
        
        f.write("| File | Subtitles | Violations | Errors | Auto-Fixable |\n")
        f.write("|------|-----------|------------|--------|-------------|\n")
        
        for report in sorted_reports:
            if report.violation_count == 0:
                continue
            filename = os.path.basename(report.filepath)
            f.write(f"| {filename[:60]}... | {report.total_subtitles} | {report.violation_count} | {report.error_count} | {report.auto_fixable_count} |\n")
        
        f.write("\n")
        
        # Sample Violations (first 50)
        f.write("## Sample Violations (First 50)\n\n")
        count = 0
        for report in sorted_reports:
            if count >= 50:
                break
            for v in report.violations[:10]:  # Max 10 per file
                if count >= 50:
                    break
                filename = os.path.basename(report.filepath)
                f.write(f"### [{v.rule}] {filename} - Subtitle #{v.subtitle_index}\n\n")
                f.write(f"- **Message:** {v.message}\n")
                f.write(f"- **Severity:** {v.severity}\n")
                if v.current_value:
                    f.write(f"- **Current:** {v.current_value}\n")
                if v.limit_value:
                    f.write(f"- **Limit:** {v.limit_value}\n")
                f.write(f"- **Auto-fixable:** {'Yes' if v.auto_fixable else 'No'}\n")
                if v.fix_suggestion:
                    f.write(f"- **Suggestion:** {v.fix_suggestion}\n")
                f.write("\n")
                count += 1
        
        # Recommendations
        f.write("## Recommendations\n\n")
        
        if summary["total_auto_fixable"] > 0:
            f.write("### Phase 1: Auto-Fix (Immediate)\n\n")
            f.write(f"**{summary['total_auto_fixable']} violations** can be automatically fixed:\n\n")
            for rule, data in summary["violations_by_rule"].items():
                if data["auto_fixable"] > 0:
                    f.write(f"- **{rule}:** {data['auto_fixable']} auto-fixable instances\n")
            f.write("\n")
        
        manual_count = summary['total_violations'] - summary['total_auto_fixable']
        if manual_count > 0:
            f.write("### Phase 2: Manual Review (Requires Attention)\n\n")
            f.write(f"**{manual_count} violations** require manual intervention:\n\n")
            for rule, data in summary["violations_by_rule"].items():
                manual = data["count"] - data["auto_fixable"]
                if manual > 0:
                    f.write(f"- **{rule}:** {manual} instances need manual review\n")
            f.write("\n")
        
        f.write("---\n")
        f.write("*Report generated by Netflix Subtitle Standards Validator*\n")


def main():
    parser = argparse.ArgumentParser(description="Validate SRT files against Netflix standards")
    parser.add_argument("--pairs-dir", default="pairs", help="Directory containing paired SRT files")
    parser.add_argument("--output", default="netflix_validation_report", help="Output filename (without extension)")
    parser.add_argument("--children", action="store_true", help="Use children's reading speed limit (15 CPS)")
    parser.add_argument("--json", action="store_true", help="Also output JSON report")
    args = parser.parse_args()
    
    max_cps = MAX_CPS_CHILDREN if args.children else MAX_CPS_ADULT
    
    print(f"Scanning for English SRT files in {args.pairs_dir}/...")
    srt_files = find_en_srt_files(args.pairs_dir)
    print(f"Found {len(srt_files)} files")
    
    print("\nValidating files...")
    reports = []
    for i, filepath in enumerate(srt_files):
        report = validate_file(filepath, max_cps)
        reports.append(report)
        if (i + 1) % 20 == 0:
            print(f"  Processed {i + 1}/{len(srt_files)} files...")
    
    print(f"\nGenerating summary...")
    summary = generate_summary(reports)
    
    # Generate Markdown report
    md_path = f"{args.output}.md"
    generate_markdown_report(reports, summary, md_path)
    print(f"Markdown report: {md_path}")
    
    # Generate JSON report if requested
    if args.json:
        json_path = f"{args.output}.json"
        json_data = {
            "summary": summary,
            "files": [
                {
                    "filepath": r.filepath,
                    "total_subtitles": r.total_subtitles,
                    "violation_count": r.violation_count,
                    "error_count": r.error_count,
                    "warning_count": r.warning_count,
                    "auto_fixable_count": r.auto_fixable_count,
                    "violations": [
                        {
                            "rule": v.rule,
                            "subtitle_index": v.subtitle_index,
                            "line_number": v.line_number,
                            "message": v.message,
                            "severity": v.severity,
                            "auto_fixable": v.auto_fixable,
                            "fix_suggestion": v.fix_suggestion,
                            "current_value": v.current_value,
                            "limit_value": v.limit_value,
                        }
                        for v in r.violations
                    ]
                }
                for r in reports
            ]
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        print(f"JSON report: {json_path}")
    
    # Print summary
    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    print(f"Files analyzed:        {summary['total_files']}")
    print(f"Total subtitles:       {summary['total_subtitles']}")
    print(f"Files with issues:     {summary['files_with_violations']} ({summary['files_with_violations']/summary['total_files']*100:.1f}%)")
    print(f"Clean files:           {summary['files_clean']}")
    print(f"Total violations:      {summary['total_violations']}")
    print(f"  - Errors:            {summary['total_errors']}")
    print(f"  - Warnings:          {summary['total_warnings']}")
    print(f"Auto-fixable:          {summary['total_auto_fixable']} ({summary['total_auto_fixable']/max(summary['total_violations'],1)*100:.1f}%)")
    print(f"Manual review needed:  {summary['total_violations'] - summary['total_auto_fixable']}")
    print("="*60)
    
    print("\nViolations by rule:")
    for rule, data in sorted(summary["violations_by_rule"].items(), key=lambda x: -x[1]["count"]):
        auto = data["auto_fixable"]
        manual = data["count"] - auto
        print(f"  {rule:25} {data['count']:5} total  ({auto} auto, {manual} manual)")


if __name__ == "__main__":
    main()
