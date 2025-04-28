# app.py
import logging
import os
import secrets
from flask import Flask, render_template, request, jsonify, session as flask_session, redirect, url_for
from threading import Lock # Import Lock for history dictionary

# --- ADK/Agent Imports ---
# (Keep existing imports)
try:
    from google.adk.sessions import InMemorySessionService
    from google.adk.runners import Runner
    from google.genai import types as genai_types
    from google.adk.events import Event
except ImportError:
    print("ERROR: Failed to import necessary ADK/GenAI components in app.py.")
    exit(1)
from agent.data_manager import DataManager
from agent.sales_agent_logic import SalesFlowAgent

# --- Basic Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

# --- Logging Setup ---
app.logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s') # Set root logger to DEBUG


# --- Global ADK/Agent Setup ---
CSV_FILENAME = "leads.csv"
APP_NAME = "sales_agent_app"
WEB_USER_ID = "flask_user"

try:
    data_manager_main = DataManager(filename=CSV_FILENAME)
    session_service_main = InMemorySessionService()
    sales_agent_main = SalesFlowAgent(name="SalesFlowAgent", data_manager=data_manager_main)
    runner_main = Runner(
        agent=sales_agent_main,
        app_name=APP_NAME,
        session_service=session_service_main
    )
    app.logger.info("ADK Runner and Agent initialized successfully.")
except Exception as e:
    app.logger.error(f"Failed to initialize ADK components: {e}", exc_info=True)
    runner_main = None

# --- Server-Side Chat History Storage ---
# WARNING: This dictionary is lost if the server restarts!
chat_histories = {}
history_lock = Lock() # Lock for safe concurrent access to the history dict

# Note: Follow-up thread NOT started for web simplicity

# --- Flask Routes ---

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/start_chat', methods=['POST'])
def start_chat():
    if not runner_main: return "Error: Agent Runner not initialized.", 500

    lead_id = request.form.get('lead_id', '').strip()
    lead_name = request.form.get('lead_name', '').strip()
    if not lead_id or not lead_name: return "Error: Lead ID and Name are required.", 400

    session_id = lead_id
    flask_session['lead_id'] = lead_id # Keep lead_id associated with browser session
    app.logger.info(f"Flask session set for lead_id: {lead_id}")

    session = session_service_main.get_session(app_name=APP_NAME, user_id=WEB_USER_ID, session_id=session_id)
    initial_state = {"name": lead_name}
    if not session:
        app.logger.info(f"Creating new ADK session: {session_id}")
        session = session_service_main.create_session(app_name=APP_NAME, user_id=WEB_USER_ID, session_id=session_id, state=initial_state)
        if not session: return "Error creating agent session.", 500
        # Initialize history for new session
        with history_lock:
            chat_histories[session_id] = []
    else:
        app.logger.warning(f"ADK Session {session_id} already exists. Updating name.")
        if hasattr(session.state, 'update'): session.state.update(initial_state)
        # Ensure history exists if session was somehow persisted without it
        with history_lock:
            if session_id not in chat_histories:
                 chat_histories[session_id] = []


    try:
        app.logger.info(f"Running initial turn for session: {session_id}")
        events = runner_main.run(user_id=WEB_USER_ID, session_id=session_id, new_message=None)

        initial_messages = []
        for event in events:
            if event.content and event.content.parts:
                 msg_text = event.content.parts[0].text
                 initial_messages.append({"author": "Agent", "text": msg_text})

        # Store initial messages in server-side dictionary
        with history_lock:
            # Overwrite history on trigger? Or append? Let's overwrite for simplicity on trigger.
            chat_histories[session_id] = initial_messages
        app.logger.info(f"Initial messages stored for {lead_id}: {initial_messages}")

    except Exception as e:
        app.logger.error(f"Error running initial agent turn for {session_id}: {e}", exc_info=True)
        return f"Error starting conversation: {e}", 500

    # No longer need to store history in Flask session cookie
    # flask_session['chat_history'] = initial_messages

    return redirect(url_for('chat'))

@app.route('/chat', methods=['GET'])
def chat():
    lead_id = flask_session.get('lead_id')
    if not lead_id: return redirect(url_for('index'))

    # Retrieve history from server-side dictionary
    with history_lock:
        chat_history = chat_histories.get(lead_id, []) # Get history or empty list

    return render_template('chat.html', lead_id=lead_id, history=chat_history)

@app.route('/send_message', methods=['POST'])
def send_message():
    if not runner_main: return jsonify({"error": "Agent Runner not initialized."}), 500

    lead_id = flask_session.get('lead_id')
    if not lead_id: return jsonify({"error": "No active session found. Please start again."}), 400

    data = request.get_json()
    user_text = data.get('message', '').strip()
    if not user_text: return jsonify({"error": "Empty message received."}), 400

    # Append user message to server-side history
    with history_lock:
        if lead_id not in chat_histories: chat_histories[lead_id] = [] # Initialize if missing
        chat_histories[lead_id].append({"author": "User", "text": user_text})


    try:
        user_content = genai_types.Content(role='user', parts=[genai_types.Part(text=user_text)])
        app.logger.info(f"Running turn for {lead_id} with message: '{user_text}'")
        events = runner_main.run(user_id=WEB_USER_ID, session_id=lead_id, new_message=user_content)

        agent_responses = []
        with history_lock: # Lock while potentially modifying history
             if lead_id not in chat_histories: chat_histories[lead_id] = [] # Safety check
             for event in events:
                 if event.content and event.content.parts:
                     response_text = event.content.parts[0].text
                     response_obj = {"author": "Agent", "text": response_text}
                     agent_responses.append(response_obj)
                     # Append agent response to server-side history
                     chat_histories[lead_id].append(response_obj)

        app.logger.info(f"Agent responses for {lead_id}: {agent_responses}")
        return jsonify({"responses": agent_responses})

    except Exception as e:
        app.logger.error(f"Error running agent turn for {lead_id}: {e}", exc_info=True)
        return jsonify({"error": f"Error processing message: {e}"}), 500

# --- Run Flask App ---
if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)