#!/usr/bin/env python3
"""Build a reconstructable PL-900 question bank from pl-900.pdf.

Native PDF text supplies stems, ordinary choices, answers, and explanations.
The embedded JPEG answer-area images are OCRed separately; their normalized
text boxes are retained for visual question types so a UI can reconstruct the
tables even when the PDF does not expose their text natively.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image


ROOT = Path(__file__).resolve().parent
PDF_PATH = ROOT / "pl-900.pdf"
OUTPUT_PATH = ROOT / "questions.json"
OCR_PATH = Path("/tmp/pl900_ocr.json")
IMAGE_DIR = Path("/tmp/pl900_images")

# Items whose completed answer images use outlines or other marks that OCR does
# not encode as text. Values are transcribed from those completed answer areas
# (and cross-checked against the supplied explanations).
VISUAL_ANSWER_OVERRIDES = {
    7: ["Power Apps portal", "Common Data Service", "Power Automate", "Power BI"],
    16: ["Environment", "Environment"],
    17: ["3"],
    18: ["Yes", "Yes"],
    22: ["One per environment", "One per environment"],
    31: ["Yes", "No", "No"],
    38: ["Yes", "Yes"],
    39: [
        "Import data into Common Data Service",
        "Train the model",
        "Publish the model",
        "Use the model in Power Apps or Power Automate",
    ],
    42: ["Power Apps", "Microsoft Dataverse", "Power Automate"],
    53: ["Power Automate flow", "Connector"],
    56: ["Yes", "Yes"],
    68: ["Power BI Service only", "Power BI Service only"],
    85: ["Modeling view", "Import"],
    119: ["Yes", "Yes"],
    123: ["Microsoft Azure", "Microsoft Flow"],
    133: ["Connectors", "AI Builder", "Portals"],
    143: ["Data", "UI", "Logic", "UI"],
    147: ["No", "No"],
    161: ["Yes", "No"],
    168: ["Yes", "Yes", "Yes"],
    177: ["No", "No"],
    181: ["Create an automated flow", "Add the triggers", "Create a Power Apps app", "Add the actions"],
    196: ["Entity", "Action"],
    198: ["No", "Yes"],
    200: ["Yes", "Yes"],
    202: ["Yes", "No", "No"],
    206: ["Yes", "Yes"],
    217: ["No", "Yes", "Yes"],
    224: ["Trigger", "Action", "Action"],
    228: ["Trigger", "Action"],
    230: ["Yes", "Yes"],
    244: ["Canvas app", "Microsoft Power BI", "Microsoft Power Automate"],
    245: ["Yes", "No"],
    254: ["Canvas app", "Canvas app"],
    273: ["AI Builder"],
    275: ["Key Performance Indicator (KPI) analysis", "Anomalies", "Trends"],
    279: ["Trigger phrase", "Show a message node", "Entity", "Action"],
    281: ["Yes", "Yes", "Yes"],
    291: ["Desktop flow"],
    292: ["Dashboard", "Report"],
    299: ["Gallery", "Function", "Object detection"],
    300: ["CompanyHolidaysGallery", "Play", "Formula"],
    305: ["Personal", "Public"],
    307: ["Desktop flow", "Instant cloud flow", "Automated cloud flow"],
    309: ["Power Apps app"],
    310: ["Copilot"],
    317: ["Dataverse"],
    318: ["No", "Yes", "Yes"],
    320: ["Yes", "No"],
    326: ["Pipelines"],
    334: ["natural language"],
    347: ["Power Automate"],
    349: [
        "Open the Power Automate maker portal",
        "Describe the automation to Copilot",
        "Generate the flow",
        "Review the flow connections and test the automation",
        "Approve the automation",
    ],
    350: ["filter"],
    351: [
        "Update the drop-down control Items property",
        "Save the app",
        "Share the app with the receptionist",
        "Grant the receptionist a security role",
    ],
    356: ["Power BI", "Dataverse for Teams", "Power Apps"],
    358: ["Yes", "No", "No"],
    363: ["Custom connector", "Standard connector"],
    366: ["Copilot for site creation"],
    367: ["Co-owner permissions"],
    374: ["Power Automate flow", "Scheduled cloud flow"],
    376: ["Connectors"],
    377: ["Manually trigger a flow", "Get current location", "Send me a mobile notification"],
}


def clean_lines(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).replace("\ufffd", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def compact_prose(text: str) -> str:
    """Join PDF line wrapping while retaining useful list/paragraph breaks."""
    lines = clean_lines(text).splitlines()
    if not lines:
        return ""
    out: list[str] = []
    for line in lines:
        starts_item = bool(
            re.match(r"^(?:[-*•✔✅]|\d+[.)]|Box\s+\d+|Reference:|Why\b|NOTE:)", line, re.I)
        )
        prev_ends = out and re.search(r"[.!?:;)]$", out[-1])
        if not out or starts_item or prev_ends or out[-1].startswith("http"):
            out.append(line)
        else:
            out[-1] += " " + line
    return "\n".join(out)


def norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_choice_block(pre_answer: str) -> tuple[str, list[dict[str, str]]]:
    matches = list(re.finditer(r"(?m)^([A-H])\.\s*(.*)$", pre_answer))
    start_index = None
    for i, match in enumerate(matches):
        if match.group(1) != "A":
            continue
        labels = [match.group(1)]
        for nxt in matches[i + 1 :]:
            expected = chr(ord(labels[-1]) + 1)
            if nxt.group(1) == expected:
                labels.append(nxt.group(1))
            elif len(labels) >= 2:
                break
        if len(labels) >= 2:
            start_index = i
    if start_index is None:
        return compact_prose(pre_answer), []

    selected = []
    expected_ord = ord("A")
    for match in matches[start_index:]:
        if ord(match.group(1)) != expected_ord:
            break
        selected.append(match)
        expected_ord += 1

    stem = pre_answer[: selected[0].start()].strip()
    choices = []
    for i, match in enumerate(selected):
        end = selected[i + 1].start() if i + 1 < len(selected) else len(pre_answer)
        value = match.group(2) + "\n" + pre_answer[match.end() : end]
        choices.append({"label": match.group(1), "text": compact_prose(value)})
    return compact_prose(stem), choices


def parse_letter_answer(value: str, valid_labels: set[str]) -> list[str]:
    first = clean_lines(value).splitlines()[0] if clean_lines(value) else ""
    prefix = re.match(r"^([A-H](?:\s*[,/&-]?\s*[A-H])*)\b", first)
    if not prefix:
        return []
    letters = re.findall(r"[A-H]", prefix.group(1))
    return [letter for letter in letters if letter in valid_labels]


def split_answer(body: str) -> tuple[str, str, str]:
    match = re.search(r"(?m)^Answer:\s*(.*)$", body)
    if not match:
        return body, "", ""
    pre = body[: match.start()].strip()
    after = (match.group(1) + "\n" + body[match.end() :]).strip()
    explanation_match = re.search(r"(?m)^Explanation:\s*$", after)
    if explanation_match:
        raw_answer = after[: explanation_match.start()].strip()
        explanation = after[explanation_match.end() :].strip()
    else:
        raw_answer = after
        explanation = ""
    return pre, raw_answer, explanation


def topic_for(text: str) -> str:
    low = text.lower()
    categories = [
        ("Microsoft Copilot Studio", {"copilot studio": 6, "power virtual agents": 6, "chatbot": 2, "bot ": 1}),
        ("Power Pages", {"power pages": 6, "power apps portal": 6, "portal template": 3, "website": 1}),
        ("Power Automate", {"power automate": 6, "cloud flow": 4, "desktop flow": 4, "workflow": 2, "trigger": 1}),
        ("Power BI", {"power bi": 6, "dashboard": 2, "visualization": 1, "dataset": 1, "report": 1}),
        ("AI Builder", {"ai builder": 6, "prebuilt model": 3, "prediction model": 3, "object detection": 3}),
        ("Microsoft Dataverse", {"dataverse": 6, "common data service": 6, "entity": 2, "relationship": 1}),
        ("Power Apps", {"power apps": 6, "canvas app": 4, "model-driven": 4, "model driven": 4, "app designer": 2}),
        ("Power Platform", {"power platform": 5, "connector": 1, "environment": 1, "solution": 1}),
        ("Dynamics 365", {"dynamics 365": 6, "sales hub": 4, "customer service": 2}),
    ]
    scores = [(sum(low.count(term) * weight for term, weight in terms.items()), -i, name) for i, (name, terms) in enumerate(categories)]
    score, _, name = max(scores)
    return name if score else "Power Platform"


def ocr_elements(record: dict) -> list[dict]:
    elements = []
    for line in record.get("lines", []):
        text = clean_lines(line.get("text", ""))
        if not text:
            continue
        elements.append(
            {
                "text": text,
                "x": round(float(line["x"]), 5),
                "y": round(float(line["y"]), 5),
                "w": round(float(line["w"]), 5),
                "h": round(float(line["h"]), 5),
            }
        )
    return elements


def image_similarity(a: dict, b: dict) -> float:
    a_lines = a["elements"]
    b_lines = b["elements"]
    if not a_lines or not b_lines:
        return -1
    score = 0.0
    for item in a_lines:
        n = norm_text(item["text"])
        candidates = [x for x in b_lines if norm_text(x["text"]) == n and n]
        if candidates:
            dist = min(abs(item["x"] - x["x"]) + abs(item["y"] - x["y"]) for x in candidates)
            score += 2.0 if dist < 0.08 else 0.5
    size_penalty = abs(a["width"] - b["width"]) / max(a["width"], b["width"], 1)
    return score / max(len(a_lines), len(b_lines)) - size_penalty


def pair_visuals(visuals: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    if len(visuals) < 2:
        return visuals[:1], [], visuals[1:]
    best_pair = None
    best_score = -99.0
    for i in range(len(visuals) - 1):
        score = image_similarity(visuals[i], visuals[i + 1])
        if score > best_score:
            best_score = score
            best_pair = (i, i + 1)
    assert best_pair is not None
    i, j = best_pair
    context = [v for k, v in enumerate(visuals) if k not in (i, j)]
    return [visuals[i]], [visuals[j]], context


def moved_answer_items(source: list[dict], answer: list[dict]) -> list[str]:
    if not source or not answer:
        return []
    src = source[0]["elements"]
    ans = answer[0]["elements"]
    ignored = {
        "answer area", "answer area statements", "statement", "statements", "answer",
        "answer choice", "requirement", "requirements", "component", "tool", "action",
        "implementation mechanism", "form type", "result", "purpose", "yes", "no",
    }
    moved = []
    for item in ans:
        n = norm_text(item["text"])
        if not n or n in ignored or len(n) == 1:
            continue
        same_position = any(
            norm_text(s["text"]) == n
            and abs(s["x"] - item["x"]) + abs(s["y"] - item["y"]) < 0.08
            for s in src
        )
        if same_position:
            continue
        # Selections typically appear in the right half of the completed table.
        if item["x"] >= 0.48 or any(norm_text(s["text"]) == n and s["x"] < item["x"] - 0.15 for s in src):
            moved.append(item)
    moved.sort(key=lambda x: (-x["y"], x["x"]))
    result = []
    for item in moved:
        if item["text"] not in result:
            result.append(item["text"])
    return result


def red_components(image: Image.Image) -> list[tuple[int, int, int, int]]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    points = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if (lambda c: c[0] > 165 and c[0] > c[1] + 45 and c[0] > c[2] + 35)(rgb.getpixel((x, y)))
    }
    components = []
    while points:
        seed = points.pop()
        stack = [seed]
        xs = [seed[0]]
        ys = [seed[1]]
        while stack:
            x, y = stack.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        stack.append(neighbor)
                        xs.append(neighbor[0])
                        ys.append(neighbor[1])
        if len(xs) >= 12:
            components.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return components


def highlighted_answer_items(question_type: str, answer: list[dict]) -> list[str]:
    if not answer:
        return []
    visual = answer[0]
    path = IMAGE_DIR / f'{visual["image_id"]}.jpeg'
    image = Image.open(path).convert("RGB")
    width, height = image.size
    elements = visual["elements"]

    components = red_components(image)
    if question_type == "hotspot_yes_no" and components:
        headers = {
            norm_text(e["text"]): e["x"] + e["w"] / 2
            for e in elements
            if norm_text(e["text"]) in {"yes", "no"}
        }
        if len(headers) == 2:
            selected = []
            for x0, y0, x1, y1 in sorted(components, key=lambda box: box[1]):
                # Ignore tiny red marks outside the Yes/No selection columns.
                center_x = ((x0 + x1) / 2) / width
                nearest = min(headers, key=lambda key: abs(headers[key] - center_x))
                if abs(headers[nearest] - center_x) < 0.18:
                    selected.append(nearest.title())
            if selected:
                return selected

    hits = []
    pixels = image.load()
    for element in elements:
        x0 = max(0, int(element["x"] * width))
        x1 = min(width, int((element["x"] + element["w"]) * width))
        y0 = max(0, int((1 - element["y"] - element["h"]) * height))
        y1 = min(height, int((1 - element["y"]) * height))
        if x1 <= x0 or y1 <= y0:
            continue
        total = (x1 - x0) * (y1 - y0)
        green = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b = pixels[x, y]
                if g > r + 7 and g > b + 4 and g > 140:
                    green += 1
        if green / total >= 0.25:
            hits.append((y0, element["text"]))

    # Red outlined dropdown/radio answers: associate each outline with OCR on the same row.
    for x0, y0, x1, y1 in components:
        center_y = (y0 + y1) / 2
        row = []
        for element in elements:
            ey = (1 - element["y"] - element["h"] / 2) * height
            if abs(ey - center_y) <= max(14, (y1 - y0) * 0.8):
                row.append(element)
        if row:
            # Prefer text horizontally enclosed by or immediately next to the outline.
            chosen = min(row, key=lambda e: abs((e["x"] + e["w"] / 2) * width - (x0 + x1) / 2))
            hits.append((int(center_y), chosen["text"]))

    hits.sort(key=lambda pair: pair[0])
    result = []
    for _, text in hits:
        n = norm_text(text)
        if n and n not in {"answer area", "yes", "no"}:
            result.append(text)
    return result


def extract_yes_no(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(r"(?i)(?:Yes|No)+", compact):
        return [m.group(0).title() for m in re.finditer(r"Yes|No", compact, re.I)]
    return [m.group(0).title() for m in re.finditer(r"\b(?:Yes|No)\b", text, re.I)]


def complex_correct(
    question_type: str, raw_answer: str, explanation: str, source: list[dict], answer: list[dict]
) -> list[str]:
    if raw_answer:
        yes_no = extract_yes_no(raw_answer)
        if yes_no:
            return yes_no
        # Preserve explicit target-to-answer mappings from newer questions.
        chunks = re.split(r"(?<=[a-z0-9)])(?=[A-Z][A-Za-z ]{2,}:)", compact_prose(raw_answer))
        chunks = [x.strip() for x in chunks if x.strip()]
        if chunks:
            return chunks

    boxes = re.findall(r"(?im)^Box\s*\d+\s*:\s*([^\n]+)", explanation)
    if boxes:
        return [re.split(r"\s+[–—-]\s*", x.strip())[0].rstrip(" .") for x in boxes]

    if question_type == "hotspot_yes_no":
        patterns = [
            r"(?im)\bAnswer:\s*(Yes|No)\b",
            r"(?im)→\s*(Yes|No)\b",
            r"(?im)^\s*(Yes|No)\s*[:.]",
            r"(?im)\((Yes|No)\)\s*:",
            r"(?im)This statement is\s*(Yes|No)\b",
        ]
        for pattern in patterns:
            values = re.findall(pattern, explanation)
            if values:
                return [value.title() for value in values]
        for line in clean_lines(explanation).splitlines()[:8]:
            if re.fullmatch(r"(?i)(?:Yes|No)(?:\s*[,./-]?\s*(?:Yes|No))+[.]?", line):
                return extract_yes_no(line)

    numbered = re.findall(r"(?im)^\s*\d+[.)]\s*(Yes|No)\b", explanation)
    if numbered:
        return [x.title() for x in numbered]

    # Some source explanations begin with a bare Yes/No answer list.
    leading = []
    for line in clean_lines(explanation).splitlines()[:8]:
        if re.fullmatch(r"Yes|No", line, re.I):
            leading.append(line.title())
        elif leading:
            break
    if leading:
        return leading

    form_types = re.findall(r"(?im)^\s*(?:✅\s*)?Form type:\s*([^\n]+)", explanation)
    if form_types:
        return [x.strip() for x in form_types]

    choices = re.findall(r"(?im)^\s*Choice:\s*([^\n]+)", explanation)
    if choices:
        return [x.strip().rstrip(".") for x in choices]

    numbered_values = re.findall(r"(?im)^\s*\d+\s*[.:]\s*([^\n]+)", explanation)
    if numbered_values:
        cleaned = [x.strip().rstrip(".") for x in numbered_values]
        if all(len(x) <= 90 for x in cleaned):
            return cleaned

    correct_answer = re.findall(r"(?im)^(?:Correct answer is\s+|Correct answer:\s*)([^\n]+)", explanation)
    if correct_answer:
        return [x.strip().rstrip(".") for x in correct_answer]

    highlighted = highlighted_answer_items(question_type, answer)
    if highlighted:
        return highlighted

    moved = moved_answer_items(source, answer)
    if moved:
        return moved

    # Finally, use concise standalone explanation lines that exactly match a
    # visible answer-area term (common in older items with blank answer text).
    candidate_norms = {
        norm_text(element["text"]): element["text"]
        for visual in source
        for element in visual["elements"]
        if 1 < len(norm_text(element["text"])) <= 70
    }
    matches = []
    for line in clean_lines(explanation).splitlines():
        n = norm_text(re.sub(r"^\d+[.)]\s*", "", line).rstrip("."))
        if n in candidate_norms:
            matches.append(candidate_norms[n])
    return matches


def source_items_for(question_type: str, source: list[dict]) -> list[str]:
    if not source:
        return []
    elements = source[0]["elements"]
    if question_type == "hotspot_yes_no":
        return ["Yes", "No"]
    ignored = {
        "answer area", "select and place", "statement", "statements", "answer", "answer choice",
        "requirement", "requirements", "component", "components", "tool", "tools", "action", "actions",
        "purpose", "result", "yes", "no", "implementation mechanism", "implementation mechanisms",
    }
    values = []
    for item in elements:
        n = norm_text(item["text"])
        if not n or n in ignored or len(n) <= 1:
            continue
        if question_type.startswith("drag_and_drop") and item["x"] > 0.32:
            continue
        if item["text"] not in values:
            values.append(item["text"])
    return values


def statement_items(source: list[dict]) -> list[str]:
    if not source:
        return []
    elements = source[0]["elements"]
    rows = []
    for item in elements:
        n = norm_text(item["text"])
        if n in {"answer area", "statement", "statements", "yes", "no"} or len(n) <= 1:
            continue
        if item["x"] < 0.72:
            rows.append(item)
    rows.sort(key=lambda x: (-x["y"], x["x"]))
    return [x["text"] for x in rows]


def main() -> None:
    if not OCR_PATH.exists():
        raise SystemExit(f"Missing OCR data: {OCR_PATH}")
    document = pymupdf.open(PDF_PATH)
    ocr_records = json.loads(OCR_PATH.read_text(encoding="utf-8"))
    ocr_by_xref = {}
    for record in ocr_records:
        match = re.search(r"img-(\d+)\.jpeg$", record["path"])
        if match:
            ocr_by_xref[int(match.group(1))] = record

    blocks = []
    markers = []
    answer_positions = {}
    for page_index, page in enumerate(document):
        page_blocks = page.get_text("blocks", sort=True)
        for block in page_blocks:
            pos = page_index * 1000 + block[1]
            text = block[4].strip()
            blocks.append((pos, text))
            marker = re.match(r"Question: (\d+)\s*\nCertyIQ", text)
            if marker:
                markers.append((pos, int(marker.group(1))))

    # Build globally ordered native text, then split at the 382 question headers.
    all_text = "\n".join(text for _, text in sorted(blocks))
    marker_matches = list(re.finditer(r"(?m)^Question: (\d+)\s*\nCertyIQ\s*$", all_text))
    if [int(m.group(1)) for m in marker_matches] != list(range(1, 383)):
        raise RuntimeError("Question markers are missing, duplicated, or out of order")

    bodies = {}
    for i, marker in enumerate(marker_matches):
        number = int(marker.group(1))
        end = marker_matches[i + 1].start() if i + 1 < len(marker_matches) else len(all_text)
        body = all_text[marker.end() : end].strip()
        if number == 382:
            body = re.split(r"(?m)^Thank you$", body)[0].strip()
        bodies[number] = body

    # Map every OCRed JPEG occurrence to the question whose header precedes it.
    image_occurrences = defaultdict(list)
    marker_index = 0
    current_question = None
    for page_index, page in enumerate(document):
        occurrences = []
        for image in page.get_images(full=True):
            xref = image[0]
            if xref not in ocr_by_xref:
                continue
            for rect in page.get_image_rects(xref):
                occurrences.append((page_index * 1000 + rect.y0, xref, page_index + 1))
        for position, xref, page_number in sorted(occurrences):
            while marker_index < len(markers) and markers[marker_index][0] <= position:
                current_question = markers[marker_index][1]
                marker_index += 1
            if current_question:
                image_occurrences[current_question].append((position, xref, page_number))

    questions = []
    for number in range(1, 383):
        body = bodies[number]
        pre_answer, raw_answer, explanation = split_answer(body)
        is_drag = bool(re.search(r"(?im)^DRAG DROP\s*-?", pre_answer))
        is_hotspot = bool(re.search(r"(?im)^HOTSPOT\s*-?", pre_answer) or "Hot Area:" in pre_answer)
        is_visual = is_drag or is_hotspot or (
            "select the answer that correctly completes the sentence" in pre_answer.lower()
            and len(image_occurrences.get(number, [])) >= 2
        )

        visuals = []
        for _, xref, page_number in image_occurrences.get(number, []):
            path = IMAGE_DIR / f"img-{xref}.jpeg"
            with Image.open(path) as image:
                width, height = image.size
            visuals.append(
                {
                    "image_id": f"img-{xref}",
                    "page": page_number,
                    "width": width,
                    "height": height,
                    "elements": ocr_elements(ocr_by_xref[xref]),
                }
            )

        if is_visual:
            pre_answer = re.sub(r"(?im)^(?:DRAG DROP|HOTSPOT)\s*-?\s*", "", pre_answer).strip()
            pre_answer = re.sub(r"(?im)^(?:Hot Area|Select and Place):\s*$", "", pre_answer).strip()
            stem = compact_prose(pre_answer)
            source, answer, context = pair_visuals(visuals)
            source_texts = [x["text"] for v in source for x in v["elements"]]
            yes_no = "yes" in {x.lower() for x in source_texts} and "no" in {x.lower() for x in source_texts}
            if is_drag and re.search(r"\b(?:in which order|arrange them|sequence)\b", stem, re.I):
                question_type = "drag_and_drop_ordering"
            elif is_drag:
                question_type = "drag_and_drop"
            elif yes_no or re.search(r"select Yes if", stem, re.I):
                question_type = "hotspot_yes_no"
            else:
                question_type = "hotspot"

            correct = VISUAL_ANSWER_OVERRIDES.get(
                number, complex_correct(question_type, raw_answer, explanation, source, answer)
            )
            source_items = source_items_for(question_type, source)
            choices = [
                {"label": chr(ord("A") + i) if i < 26 else str(i + 1), "text": text}
                for i, text in enumerate(source_items)
            ]
            interaction = {
                "source_items": source_items,
                "statements": statement_items(source) if question_type == "hotspot_yes_no" else [],
                "source_visuals": source,
                "answer_visuals": answer,
                "context_visuals": context,
            }
            item = {
                "q": stem,
                "choices": choices,
                "correct": correct,
                "multi": len(correct) > 1,
                "question_type": question_type,
                "explanation": compact_prose(explanation),
                "topic": topic_for(stem + " " + explanation),
                "interaction": interaction,
            }
        else:
            stem, choices = parse_choice_block(pre_answer)
            valid_labels = {choice["label"] for choice in choices}
            correct = parse_letter_answer(raw_answer, valid_labels)
            if not correct and raw_answer:
                correct = [compact_prose(raw_answer).splitlines()[0]]
            question_type = "multiple_select" if len(correct) > 1 else "multiple_choice"

            # A handful of standard questions contain a screenshot/table in the stem.
            # Keep it as structured OCR when present; omit explanation-only screenshots.
            context_visuals = visuals if visuals and re.search(r"following|depicted|shown", stem, re.I) else []
            item = {
                "q": stem,
                "choices": choices,
                "correct": correct,
                "multi": len(correct) > 1,
                "question_type": question_type,
                "explanation": compact_prose(explanation),
                "topic": topic_for(stem + " " + explanation),
            }
            if context_visuals:
                item["interaction"] = {"context_visuals": context_visuals}

        questions.append(item)

    OUTPUT_PATH.write_text(json.dumps(questions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(questions)} questions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
