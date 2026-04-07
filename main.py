from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import tempfile
import os
import subprocess
import json

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'FactLens Backend'})

@app.route('/transcribe', methods=['POST'])
def transcribe():
    try:
        data = request.json
        video_url = data.get('url', '')
        
        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400

        # Download audio from video URL using yt-dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'audio.mp3')
            
            # Use yt-dlp to extract audio
            result = subprocess.run([
                'yt-dlp',
                '--extract-audio',
                '--audio-format', 'mp3',
                '--audio-quality', '0',
                '--output', audio_path,
                '--no-playlist',
                '--socket-timeout', '30',
                video_url
            ], capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                return jsonify({'error': 'Could not download video audio: ' + result.stderr[:200]}), 400

            if not os.path.exists(audio_path):
                # yt-dlp sometimes adds extension
                for f in os.listdir(tmpdir):
                    if f.startswith('audio'):
                        audio_path = os.path.join(tmpdir, f)
                        break

            if not os.path.exists(audio_path):
                return jsonify({'error': 'Audio file not created'}), 400

            # Transcribe using OpenAI Whisper
            transcribe_result = subprocess.run([
                'whisper', audio_path,
                '--model', 'base',
                '--output_format', 'txt',
                '--output_dir', tmpdir,
                '--language', 'en'
            ], capture_output=True, text=True, timeout=120)

            # Read transcript
            transcript_path = audio_path.replace('.mp3', '.txt')
            if not os.path.exists(transcript_path):
                for f in os.listdir(tmpdir):
                    if f.endswith('.txt'):
                        transcript_path = os.path.join(tmpdir, f)
                        break

            if not os.path.exists(transcript_path):
                return jsonify({'error': 'Transcription failed'}), 400

            with open(transcript_path, 'r') as f:
                transcript = f.read().strip()

            if not transcript:
                return jsonify({'error': 'Empty transcript'}), 400

            return jsonify({'transcript': transcript})

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Video processing timed out'}), 408
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/factcheck', methods=['POST'])
def factcheck():
    try:
        data = request.json
        text = data.get('text', '')

        if not text:
            return jsonify({'error': 'No text provided'}), 400

        # Clean text
        clean = text[:1000]

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = '''You are FactLens, an AI fact-checker. Analyse this transcript or post and return ONLY valid JSON.

Text: "''' + clean + '''"

Return exactly this JSON:
{"verdict":{"type":"false","emoji":"cross","label":"FALSE","summary":"one sentence verdict"},"claims":[{"status":"bad","quote":"specific claim","explanation":"two sentence explanation.","confidence":85}],"aiGenerated":{"detected":false,"confidence":50}}

VERDICT RULES:
- true: claim is accurate and supported by evidence
- false: claim is demonstrably wrong with clear evidence
- misleading: contains truth but framed deceptively or omits important context
- unverified: cannot confirm or deny, specific figures not verifiable

emoji: check/cross/warning/question
status: ok/bad/warn/info
Max 2 claims. Return ONLY the JSON.'''

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=600,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )

        # Extract text from response
        full_text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                full_text += block.text

        # Find JSON in response
        import re
        match = re.search(r'\{[\s\S]*\}', full_text)
        if not match:
            return jsonify({'error': 'No JSON in response'}), 500

        result = json.loads(match.group(0))

        # Map emoji names to actual emojis
        emoji_map = {'check': '✅', 'cross': '🚫', 'warning': '⚠️', 'question': '🔎'}
        if result.get('verdict'):
            result['verdict']['emoji'] = emoji_map.get(result['verdict']['emoji'], '🔎')

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
