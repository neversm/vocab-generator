import os
from flask import Flask, request, jsonify, render_template
import base64
import io
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image

app = Flask(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

MODEL = "gpt-4o"

# The constant formatting rules — used no matter the input type.
BASE_RULES = """
Generate a minimum of 15 vocabulary terms and their definitions related to this subject.

CRITICAL FORMATTING RULES:
1. You MUST use "question" for the definition and "term" for the vocabulary word.
2. Every single term MUST be in ALL CAPS.
3. Do NOT include markdown blocks like ```javascript or ```text. Output raw text only.
4. Your output must be in two exact parts.

PART 1 (The JavaScript Array):
const vocabData = [
    { question: "Your definition goes here.", term: "TERM1" },
    { question: "Your definition goes here.", term: "TERM2" }
];

PART 2 (The plain text list):
Leave two blank lines after the array, then output just the terms, one on each line, in ALL CAPS.

Example of Part 2:
TERM1
TERM2
TERM3

Do not add any greetings, explanations, or extra text. Only provide Part 1 and Part 2.
"""


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate-vocab', methods=['POST'])
def generate_vocab():
    if not GITHUB_TOKEN:
        return jsonify({'error': 'Server configuration error: GITHUB_TOKEN is missing.'}), 500

    # Inputs: optional files AND/OR an optional typed topic
    files = request.files.getlist('files')
    topic = (request.form.get('topic') or '').strip()

    has_files = bool(files) and files[0].filename != ''

    if not has_files and not topic:
        return jsonify({'error': 'Type a topic/words, or upload at least one file.'}), 400

    if has_files and len(files) > 8:
        return jsonify({'error': 'Maximum limit of 8 files exceeded.'}), 400

    try:
        base64_images = []

        if has_files:
            for file in files:
                filename = file.filename.lower()

                if filename.endswith('.pdf'):
                    pdf_bytes = file.read()
                    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    for page_num in range(len(doc)):
                        if len(base64_images) >= 40:  # safety cap across all files
                            break
                        page = doc.load_page(page_num)
                        zoom_matrix = fitz.Matrix(2, 2)
                        pix = page.get_pixmap(matrix=zoom_matrix)
                        img_bytes = pix.tobytes("jpeg")
                        base64_images.append(base64.b64encode(img_bytes).decode('utf-8'))
                    doc.close()

                elif filename.endswith(('.png', '.jpg', '.jpeg')):
                    image_bytes = file.read()
                    Image.open(io.BytesIO(image_bytes)).verify()
                    base64_images.append(base64.b64encode(image_bytes).decode('utf-8'))

                else:
                    return jsonify({'error': f'Unsupported file type: {filename}'}), 400

        # Build an intro that matches whatever inputs were given
        if topic and has_files:
            intro = (
                f'The user provided this topic/keywords: "{topic}". '
                f'Also observe the provided images/documents. '
                f'Combine both to identify the subject matter.'
            )
        elif topic:
            intro = f'Generate vocabulary based on this topic/words/phrases provided by the user: "{topic}".'
        else:
            intro = 'Observe the provided images/documents. Identify the core topic, book, or subject matter.'

        prompt_text = (intro + "\n" + BASE_RULES).strip()

        content = [{"type": "text", "text": prompt_text}]
        for b64_img in base64_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            })

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=0.3,
            max_tokens=3000
        )

        vocab_result = response.choices[0].message.content.strip()

        # Clean up any markdown code blocks if the AI stubbornly adds them
        vocab_result = (vocab_result
                        .replace("```javascript\n", "")
                        .replace("```text\n", "")
                        .replace("```\n", "")
                        .replace("```", ""))

        return jsonify({'text': vocab_result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
