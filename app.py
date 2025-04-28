# app.py
# Corrected: Removed premature status update to 'declined_final' from reset helper.

import logging
import os
import secrets
import time # Import time
import threading # Import threading
from datetime import datetime, timezone, timedelta # Import datetime components
from flask import Flask, render_template, request, jsonify, session as flask_session, redirect, url_for
from threading import Lock # Import Lock

# --- ADK/Agent Imports ---
try:
    from google.adk.sessions import InMemorySessionService
    from google.adk.runners import Runner
    from google.genai import types as genai_types
    from google.adk.events import Event
except ImportError:
    print("ERROR: Failed to import necessary ADK/GenAI components in app.py.")
    exit(1)

# Import agent logic (now including follow_up_checker function)
from agent.data_manager import DataManager
from agent.sales_agent_logic import SalesFlowAgent, follow_up_checker # Import checker

# --- Basic Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

# --- Logging Setup ---
app.logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(threadName)s - %(levelname)s - [%(name)s] %(message)s')
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# --- Configuration ---
CSV_FILENAME = "leads.csv"
APP_NAME = "sales_agent_app"
WEB_USER_ID = "flask_user" # Single user ID for this simple web app
DECLINE_RESET_TIMEOUT_SECONDS = 10 # Timeout for restarting after decline

# --- Global ADK/Agent Setup ---
runner_main = None
data_manager_main = None # Initialize data_manager_main as None
try:
    data_manager_main = DataManager(filename=CSV_FILENAME) # Assign here
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

# --- Server-Side Chat History & Follow-up Storage ---
chat_histories = {}
history_lock = Lock()
pending_followups = {}
followup_lock = Lock()

# --- Start Follow-up Thread ---
# Check if data_manager was initialized successfully before starting thread
if data_manager_main:
    follow_up_thread = threading.Thread(
        target=follow_up_checker,
        # Pass the required arguments
        args=(data_manager_main, pending_followups, followup_lock),
        daemon=True,
        name="FollowUpChecker"
    )
    follow_up_thread.start()
    app.logger.info("Follow-up checker thread started.")
else:
    app.logger.error("DataManager failed to initialize. Follow-up checker NOT started.")


# --- Helper Function ---
def _run_initial_turn(session_id, lead_name, is_reset: bool = False):
    """Creates/Resets session, runs initial agent turn, returns messages."""
    # Access global instances (ensure they were initialized)
    global data_manager_main, runner_main, session_service_main, chat_histories, history_lock
    if not runner_main or not data_manager_main or not session_service_main:
        app.logger.error(f"Helper Error: Runner or DataManager or SessionService not initialized for {session_id}.")
        raise RuntimeError("Agent Runner, DataManager or SessionService not initialized.")

    app.logger.info(f"--- Helper _run_initial_turn START for {session_id}, is_reset={is_reset} ---")

    session = session_service_main.get_session(app_name=APP_NAME, user_id=WEB_USER_ID, session_id=session_id)
    initial_state = {"name": lead_name} # State to set/reset to

    with history_lock: # Lock access to chat_histories
        if not session:
            app.logger.info(f"Helper: Creating new ADK session: {session_id}")
            session = session_service_main.create_session(app_name=APP_NAME, user_id=WEB_USER_ID, session_id=session_id, state=initial_state)
            if not session:
                app.logger.error(f"Helper Error: Failed to create ADK session {session_id}")
                raise RuntimeError(f"Failed to create ADK session {session_id}")
            chat_histories[session_id] = [] # Initialize history
            app.logger.info(f"Helper: Initialized history for new session {session_id}")
        else:
            app.logger.warning(f"Helper: ADK Session {session_id} exists. Resetting state and history.")
            # Reset state and history completely
            try:
                 # Check if state is the custom State object or dict
                 if hasattr(session.state, '_value'): # It's the State object
                      session.state._value = initial_state # Reset internal value
                      session.state._delta = {} # Clear delta
                 else: # Assume it's a dict
                      session.state = initial_state # Overwrite state directly
                 app.logger.info(f"Helper: Reset ADK session state for {session_id}")
            except Exception as state_reset_err:
                 app.logger.error(f"Helper Error resetting ADK state for {session_id}: {state_reset_err}")
                 # Continue anyway, but state might be wrong

            chat_histories[session_id] = [] # Clear history
            app.logger.info(f"Helper: Cleared history for existing session {session_id}")

    app.logger.info(f"Helper: Running agent turn for {session_id}")
    events = runner_main.run(user_id=WEB_USER_ID, session_id=session_id, new_message=None)
    initial_messages = []
    for event in events:
        if event.content and event.content.parts:
             msg_text = event.content.parts[0].text
             initial_messages.append({"author": "Agent", "text": msg_text})
    app.logger.info(f"Helper: Extracted {len(initial_messages)} initial messages for {session_id}: {initial_messages}")


    with history_lock:
         if session_id not in chat_histories: chat_histories[session_id] = []
         chat_histories[session_id].extend(initial_messages) # Append new initial messages
         app.logger.info(f"Helper: Stored {len(initial_messages)} messages in server history for {session_id}")

    # --- REMOVED CSV STATUS UPDATE FROM HELPER ---
    # The follow_up_checker thread is now solely responsible for setting 'declined_final' status
    if is_reset:
        app.logger.info(f"Helper: Reset logic finished for {session_id}. CSV status NOT changed here.")
        # Clear the ADK session state fully after the reset/greeting
        adk_session_to_clear = session_service_main.get_session(app_name=APP_NAME, user_id=WEB_USER_ID, session_id=session_id)
        if adk_session_to_clear:
             try:
                 if hasattr(adk_session_to_clear.state, '_value'): adk_session_to_clear.state._value = {}; adk_session_to_clear.state._delta = {}
                 else: adk_session_to_clear.state = {}
                 app.logger.info(f"Helper: Cleared ADK session state for {session_id} after reset.")
             except Exception as clear_err:
                  app.logger.error(f"Helper: Error clearing ADK session state for {session_id}: {clear_err}")


    app.logger.info(f"--- Helper _run_initial_turn END for {session_id} ---")
    return initial_messages

# --- Flask Routes ---

@app.route('/', methods=['GET'])
def index():
    flask_session.pop('lead_id', None)
    return render_template('index.html')

@app.route('/start_chat', methods=['POST'])
def start_chat():
    lead_id = request.form.get('lead_id', '').strip()
    lead_name = request.form.get('lead_name', '').strip()
    if not lead_id or not lead_name: return "Error: Lead ID and Name are required.", 400

    flask_session['lead_id'] = lead_id
    app.logger.info(f"Flask session cookie set for lead_id: {lead_id}")

    try:
        # Call helper, indicate it's NOT a reset initially
        _run_initial_turn(lead_id, lead_name, is_reset=False)
    except Exception as e:
        app.logger.error(f"Error during start_chat for {lead_id}: {e}", exc_info=True)
        # Maybe redirect to index with error message?
        return f"Error starting conversation: {e}", 500

    return redirect(url_for('chat'))

@app.route('/chat', methods=['GET'])
def chat():
    lead_id = flask_session.get('lead_id')
    if not lead_id: return redirect(url_for('index'))

    # Check for Declined Timeout on Page Load/Refresh
    lead_data = None
    if data_manager_main: lead_data = data_manager_main.get_lead(lead_id)
    else: app.logger.error("DataManager not available in /chat route.")

    reset_occurred = False
    if lead_data and lead_data.get('status') == 'awaiting_followup_after_decline':
        ts_str = lead_data.get('last_agent_msg_ts')
        if ts_str:
            try:
                last_msg_time = datetime.fromisoformat(ts_str)
                if last_msg_time.tzinfo is None: last_msg_time = last_msg_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if now - last_msg_time > timedelta(seconds=DECLINE_RESET_TIMEOUT_SECONDS):
                    app.logger.critical(f"!!!!!! Timeout detected in /chat route for {lead_id}. Calling reset helper. !!!!!!")
                    lead_name = lead_data.get('name', f"Lead_{lead_id[:6]}")
                    try:
                         _run_initial_turn(lead_id, lead_name, is_reset=True) # Indicate reset
                         reset_occurred = True
                    except Exception as e:
                         app.logger.error(f"Error resetting conversation for {lead_id} in /chat route: {e}", exc_info=True)

            except ValueError: app.logger.warning(f"Could not parse timestamp '{ts_str}' for session {lead_id} during chat load check.")
            except Exception as e: app.logger.error(f"Error checking decline timeout for {lead_id} on chat load: {e}", exc_info=True)

    # Retrieve potentially updated history
    with history_lock:
        chat_history = chat_histories.get(lead_id, [])

    app.logger.info(f"Rendering chat page for {lead_id} with {len(chat_history)} history items. Reset occurred: {reset_occurred}")
    return render_template('chat.html', lead_id=lead_id, history=chat_history)

@app.route('/send_message', methods=['POST'])
def send_message():
    if not runner_main: return jsonify({"error": "Agent Runner not initialized."}), 500
    if not data_manager_main: return jsonify({"error": "DataManager not initialized."}), 500
    lead_id = flask_session.get('lead_id')
    if not lead_id: return jsonify({"error": "No active session found. Please start again."}), 400

    data = request.get_json()
    user_text = data.get('message', '').strip()
    if not user_text: return jsonify({"error": "Empty message received."}), 400

    # Check for Declined Timeout Before Processing Message
    lead_data = data_manager_main.get_lead(lead_id)
    if lead_data and lead_data.get('status') == 'awaiting_followup_after_decline':
        ts_str = lead_data.get('last_agent_msg_ts')
        if ts_str:
            try:
                last_msg_time = datetime.fromisoformat(ts_str)
                if last_msg_time.tzinfo is None: last_msg_time = last_msg_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if now - last_msg_time > timedelta(seconds=DECLINE_RESET_TIMEOUT_SECONDS):
                    app.logger.critical(f"!!!!!! Timeout detected in /send_message route for {lead_id}. Calling reset helper. !!!!!!")
                    lead_name = lead_data.get('name', f"Lead_{lead_id[:6]}")
                    try:
                        initial_messages = _run_initial_turn(lead_id, lead_name, is_reset=True) # Indicate reset
                        # Return the NEW greeting message, discarding user's input during timeout
                        return jsonify({"responses": initial_messages})
                    except Exception as e:
                        app.logger.error(f"Error resetting conversation for {lead_id} in /send_message: {e}", exc_info=True)
                        return jsonify({"error": f"Error restarting conversation: {e}"}), 500

            except ValueError: app.logger.warning(f"Could not parse timestamp '{ts_str}' for session {lead_id} during send_message check.")
            except Exception as e: app.logger.error(f"Error checking decline timeout for {lead_id} on send_message: {e}", exc_info=True)


    # Append user message (if not reset)
    user_msg_obj = {"author": "User", "text": user_text}
    with history_lock:
        if lead_id not in chat_histories: chat_histories[lead_id] = []
        chat_histories[lead_id].append(user_msg_obj)
        app.logger.info(f"Appended user message to history for {lead_id}")

    # Run agent turn as normal
    try:
        user_content = genai_types.Content(role='user', parts=[genai_types.Part(text=user_text)])
        app.logger.info(f"Running turn for {lead_id} with message: '{user_text}'")
        events = runner_main.run(user_id=WEB_USER_ID, session_id=lead_id, new_message=user_content)

        agent_responses = []
        with history_lock:
             if lead_id not in chat_histories: chat_histories[lead_id] = []
             for event in events:
                 if event.content and event.content.parts:
                     response_text = event.content.parts[0].text
                     response_obj = {"author": "Agent", "text": response_text}
                     agent_responses.append(response_obj)
                     chat_histories[lead_id].append(response_obj)

        app.logger.info(f"Agent responses for {lead_id}: {agent_responses}")
        return jsonify({"responses": agent_responses})

    except Exception as e:
        app.logger.error(f"Error running agent turn for {lead_id}: {e}", exc_info=True)
        error_message = f"Error processing message: {e}"
        with history_lock:
             if lead_id not in chat_histories: chat_histories[lead_id] = []
             chat_histories[lead_id].append({"author": "System", "text": error_message})
        return jsonify({"error": error_message}), 500

# --- NEW: Endpoint for Client-Side Polling (For general follow-up if enabled) ---
@app.route('/check_followup', methods=['GET'])
def check_followup():
    """Checks if there's a pending follow-up message for the current user."""
    lead_id = flask_session.get('lead_id')
    if not lead_id:
        return jsonify({"error": "No active session."}), 400

    follow_up_text = None
    # Check if lock and dict exist before using
    if 'followup_lock' in globals() and 'pending_followups' in globals():
        with followup_lock:
            # Use pop to retrieve and remove the message so it's only sent once
            follow_up_text = pending_followups.pop(lead_id, None)
    else:
        app.logger.warning("Follow-up dictionary or lock not found during check.")


    if follow_up_text:
        app.logger.info(f"Sending pending follow-up message to {lead_id}")
        followup_msg_obj = {"author": "Agent", "text": follow_up_text}
        # Append to server-side history as well
        with history_lock:
             if lead_id not in chat_histories: chat_histories[lead_id] = []
             chat_histories[lead_id].append(followup_msg_obj)
        return jsonify({"message": followup_msg_obj}) # Send object with author/text
    else:
        # No follow-up pending for this lead_id
        return jsonify({}) # Return empty JSON


# --- Run Flask App ---
if __name__ == '__main__':
    follow_up_thread_ref = None # Initialize reference
    if 'follow_up_thread' in locals() and isinstance(follow_up_thread, threading.Thread):
        follow_up_thread_ref = follow_up_thread # Store reference if thread was started

    try:
        # Use threaded=True for development. use_reloader=False prevents memory wipe on save.
        app.run(debug=True, host='127.0.0.1', port=5000, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Exiting...")
    finally:
        # --- Cleanup ---
        print("Stopping follow-up thread...")
        # Need access to the global _follow_up_running flag from the other module
        # This cross-module global flag management is awkward.
        # A better approach involves classes or signaling mechanisms.
        # For now, we can't cleanly stop the thread's loop from here.
        # sales_agent_logic._follow_up_running = False # This won't work directly

        # We can only join it for a short time if the process exits
        if follow_up_thread_ref and follow_up_thread_ref.is_alive():
             print("Waiting briefly for follow-up thread to join...")
             follow_up_thread_ref.join(timeout=1.0) # Wait up to 1 second
        print("Exited.")