# sales_agent_adk_structured.py
# Based on the StoryFlowAgent example structure (BaseAgent, InvocationContext, Runner)
# Corrected: Uses state_delta in Event actions, handles State object vs dict, fixes to_dict error

import logging
import threading
import time
import csv
import os
import copy # Import the copy module
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, TypedDict, AsyncGenerator

from typing_extensions import override

# --- Attempt to import ADK components ---
# IMPORTANT: Ensure the package providing these is installed and imports are correct
try:
    from google.adk.agents import BaseAgent
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions import InMemorySessionService, Session
    from google.adk.runners import Runner
    from google.adk.events import Event, EventActions # Import EventActions
    from google.genai import types as genai_types
    # Import the State class from its actual location (adjust path if needed)
    # Assuming it might be nested within sessions based on previous file structure
    from google.adk.sessions.state import State
except ImportError:
    print("--------------------------------------------------------------------")
    print("ERROR: Failed to import necessary ADK components.")
    print("(BaseAgent, InvocationContext, InMemorySessionService, Runner, Event, EventActions, State)")
    print("and google-generativeai.")
    print("Please ensure you have installed the correct Google ADK library and dependencies.")
    print("The specific ADK package name might vary.")
    print("Example: pip install google-generativeai <your-google-adk-package>")
    print("--------------------------------------------------------------------")
    exit(1)


# --- Configuration ---
SIMULATED_24H_DELAY_SECONDS = 30 # Seconds for testing follow-up
FOLLOW_UP_CHECK_INTERVAL_SECONDS = 5 # How often the checker runs
CSV_FILENAME = "leads.csv" # Name of the CSV file
APP_NAME = "sales_agent_app" # Name for the session service

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - [%(name)s] %(message)s'
)
logging.getLogger("google.adk").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- DataManager Class (Handles CSV Persistence) ---
class DataManager:
    """Handles thread-safe reading and writing to the leads CSV file."""
    def __init__(self, filename="leads.csv"):
        self.filename = filename
        self.lock = threading.Lock()
        self.fieldnames = [
            'lead_id', 'name', 'age', 'country', 'interest', 'status',
            'last_agent_msg_ts', 'follow_up_sent_flag'
        ]
        self._initialize_csv()
        logger.info(f"DataManager initialized for file: {self.filename}")

    def _initialize_csv(self):
        with self.lock:
            file_exists = os.path.exists(self.filename)
            is_empty = file_exists and os.path.getsize(self.filename) == 0
            if not file_exists or is_empty:
                try:
                    with open(self.filename, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames)
                        writer.writeheader()
                    logger.info(f"Initialized CSV file: {self.filename}")
                except IOError as e:
                    logger.error(f"Error initializing CSV file {self.filename}: {e}", exc_info=True)

    def _read_all(self) -> List[Dict[str, str]]:
        rows = []
        if not os.path.exists(self.filename): return rows
        try:
            with open(self.filename, 'r', newline='', encoding='utf-8-sig') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    complete_row = {field: row.get(field, '') for field in self.fieldnames}
                    rows.append(complete_row)
        except Exception as e:
             logger.error(f"Error reading CSV file {self.filename}: {e}", exc_info=True)
             return []
        return rows

    def _write_all(self, data: List[Dict[str, Any]]):
        try:
            with open(self.filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames, extrasaction='ignore')
                writer.writeheader()
                sanitized_data = []
                for row_data in data:
                     sanitized_row = {field: str(row_data.get(field, '')) for field in self.fieldnames}
                     sanitized_data.append(sanitized_row)
                writer.writerows(sanitized_data)
        except IOError as e:
            logger.error(f"Error writing to CSV file {self.filename}: {e}", exc_info=True)

    def update_lead(self, lead_data: Dict[str, Any]):
        lead_data_str = {k: str(v) if v is not None else '' for k, v in lead_data.items()}
        with self.lock:
            rows = self._read_all()
            lead_id_to_update = lead_data_str.get('lead_id')
            if not lead_id_to_update:
                 logger.error("CSV Update Error: lead_id missing in data.")
                 return
            updated = False
            update_values = {field: lead_data_str.get(field) for field in self.fieldnames if field in lead_data_str}
            for i, row in enumerate(rows):
                if row.get('lead_id') == str(lead_id_to_update):
                    rows[i] = {**row, **update_values}
                    updated = True
                    break
            if not updated:
                new_row = {field: update_values.get(field, '') for field in self.fieldnames}
                new_row['lead_id'] = str(lead_id_to_update)
                rows.append(new_row)
                logger.info(f"Adding new lead {lead_id_to_update} to CSV.")
            self._write_all(rows)
            logger.debug(f"CSV updated for lead_id: {lead_id_to_update}")

    def get_all_active_leads_for_followup(self) -> List[Dict[str, str]]:
         active_leads = []
         with self.lock:
             rows = self._read_all()
             for row in rows:
                 if row.get('status') and row['status'] not in ['secured', 'no_response', 'declined', 'completed', 'initiated']:
                     active_leads.append({
                         'lead_id': row.get('lead_id', ''),
                         'last_agent_msg_ts': row.get('last_agent_msg_ts', ''),
                         'follow_up_sent_flag': row.get('follow_up_sent_flag', 'False')
                     })
         return active_leads

# --- Conversation State Definition ---
# The ADK uses the custom 'State' class internally for ctx.session.state

# --- Custom Sales Agent ---
class SalesFlowAgent(BaseAgent):
    """Orchestrates the sales lead conversation flow."""

    data_manager: DataManager
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, data_manager: DataManager):
        """Initializes the SalesFlowAgent."""
        super().__init__(
            name=name,
            sub_agents=[],
            data_manager=data_manager
        )
        logger.info(f"SalesFlowAgent '{name}' initialized.")


    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Handles a turn in the sales conversation flow using state_delta for persistence."""
        session_id = ctx.session.id
        agent_name = self.name
        logger.info(f"--- Agent Turn Start: Session {session_id}, Agent: {agent_name} ---")

        # --- Get the State object or initial dict ---
        state_obj_or_dict = ctx.session.state # Could be State object or initial dict
        # Check if it behaves like our State class (duck typing)
        is_custom_state_obj = hasattr(state_obj_or_dict, '_value') and hasattr(state_obj_or_dict, '_delta')

        user_utterance = ""
        if ctx.session.events:
             last_event = ctx.session.events[-1]
             if last_event.content and last_event.content.role == 'user' and last_event.content.parts:
                 user_utterance = last_event.content.parts[0].text

        # --- Logging Initial State ---
        if is_custom_state_obj:
            # Use the .to_dict() method now that we expect state_obj_or_dict might be a State instance
            logger.info(f"State object at start: {state_obj_or_dict.to_dict()}")
        else:
            logger.info(f"Initial state dict at start: {state_obj_or_dict}")

        logger.info(f"User utterance: '{user_utterance}'")

        updated_csv_data = {"lead_id": session_id}
        message_to_send = None
        terminate_conversation = False
        timestamp_now_iso = datetime.now().isoformat()
        state_changes: Dict[str, Any] = {} # Track changes for the delta

        # Use a local dictionary for easier state manipulation during the turn logic
        current_turn_state = copy.deepcopy(state_obj_or_dict.to_dict() if is_custom_state_obj else state_obj_or_dict)

        # --- Handle New Conversation ---
        if "current_step" not in current_turn_state: # Check the local copy
            logger.info(f"Session {session_id} state lacks 'current_step'. Initializing.")
            lead_name = current_turn_state.get("name", f"Lead_{session_id[:6]}") # Use local copy
            logger.info(f"Using lead name: {lead_name}")

            # Calculate initial state changes needed
            state_changes = {
                "lead_id": session_id, "name": lead_name,
                "current_step": "awaiting_consent", "status": "awaiting_consent",
                "age": None, "country": None, "interest": None,
                "last_agent_msg_ts": timestamp_now_iso,
                "follow_up_sent": False
            }
            # Update the local copy for immediate use
            current_turn_state.update(state_changes)

            # Update CSV
            self.data_manager.update_lead({
                "lead_id": session_id, "name": lead_name, "status": "awaiting_consent",
                "last_agent_msg_ts": timestamp_now_iso, "follow_up_sent_flag": str(False)
            })
            message_to_send = f"Hey {lead_name}, thank you for filling out the form. I'd like to gather some information from you. Is that okay?"

        # --- Handle Existing Conversation ---
        else:
            current_step = current_turn_state.get("current_step", "initial")
            logger.info(f"Existing session {session_id}. Step: {current_step}")

            # --- Clear timer/flags in local copy and track changes ---
            current_turn_state['last_agent_msg_ts'] = None
            current_turn_state['follow_up_sent'] = False
            state_changes['last_agent_msg_ts'] = None
            state_changes['follow_up_sent'] = False

            response_lower = user_utterance.lower().strip()

            # --- State Machine Logic (updates 'current_turn_state' and 'state_changes') ---
            next_step = current_step
            next_status = current_turn_state.get('status')

            if current_step == "awaiting_consent":
                if any(consent in response_lower for consent in ["yes", "ok", "okay", "sure", "yeah", "yep", "affirmative"]):
                    next_step = "awaiting_age"; next_status = "awaiting_age"
                    message_to_send = "Great! What is your age?"
                else:
                    next_step = "declined"; next_status = "no_response"
                    message_to_send = "Alright, no problem. Have a great day!"
                    terminate_conversation = True
            elif current_step == "awaiting_age":
                 if response_lower.isdigit() and 0 < int(response_lower) < 120:
                     current_turn_state['age'] = response_lower; state_changes['age'] = response_lower
                     updated_csv_data["age"] = response_lower
                     next_step = "awaiting_country"; next_status = "awaiting_country"
                     message_to_send = "Got it. Which country are you from?"
                 else: message_to_send = "Sorry... provide age as a number (e.g., 30)?"
            elif current_step == "awaiting_country":
                 if user_utterance.strip():
                     current_turn_state['country'] = user_utterance.strip(); state_changes['country'] = user_utterance.strip()
                     updated_csv_data["country"] = user_utterance.strip()
                     next_step = "awaiting_interest"; next_status = "awaiting_interest"
                     message_to_send = "Thanks! What product or service are you interested in?"
                 else: message_to_send = "Could you please let me know which country you are from?"
            elif current_step == "awaiting_interest":
                 if user_utterance.strip():
                     current_turn_state['interest'] = user_utterance.strip(); state_changes['interest'] = user_utterance.strip()
                     updated_csv_data["interest"] = user_utterance.strip()
                     next_step = "completed"; next_status = "secured"
                     message_to_send = "Excellent, thank you for the information! We'll be in touch."
                     terminate_conversation = True
                 else: message_to_send = "Could you please tell me what product or service... interested in?"
            elif current_step in ["completed", "declined", "no_response"]:
                 logger.info(f"Conversation {session_id} already finished...")
                 message_to_send = None; terminate_conversation = True
            else: logger.warning(f"Turn for session {session_id} in unexpected state..."); message_to_send = "Sorry, I seem to have gotten confused..."

            # --- Apply step/status changes to local state and track for delta ---
            if next_step != current_step: current_turn_state['current_step'] = next_step; state_changes['current_step'] = next_step
            if next_status != current_turn_state.get('status'): current_turn_state['status'] = next_status; state_changes['status'] = next_status

        # --- Send Response ---
        event_actions = EventActions() # Default empty actions
        if message_to_send:
            logger.info(f"Preparing agent response for {session_id}: '{message_to_send}'")
            agent_content = genai_types.Content(role='model', parts=[genai_types.Part(text=message_to_send)])

            # Update timestamp in local state and track change if needed
            if any(q in message_to_send for q in ["okay?", "age?", "from?", "interested in?", "ready to continue."]):
                current_turn_state['last_agent_msg_ts'] = timestamp_now_iso; state_changes['last_agent_msg_ts'] = timestamp_now_iso
                current_turn_state['follow_up_sent'] = False; state_changes['follow_up_sent'] = False

            # --- Prepare Event with Content and Calculated State Delta ---
            if state_changes:
                 logger.debug(f"Attaching state delta to event: {state_changes}")
                 event_actions = EventActions(state_delta=copy.deepcopy(state_changes))

            yield Event(author=agent_name, content=agent_content, actions=event_actions)

        # --- Update CSV ---
        # Update CSV based on the final calculated state for this turn
        updated_csv_data["status"] = current_turn_state.get('status', 'unknown')
        updated_csv_data["last_agent_msg_ts"] = current_turn_state.get('last_agent_msg_ts', '')
        updated_csv_data["follow_up_sent_flag"] = str(current_turn_state.get('follow_up_sent', False))

        logger.info(f"Updating CSV for {session_id}. Status: {updated_csv_data['status']}")
        self.data_manager.update_lead(updated_csv_data)

        # --- State Persistence via Event Actions ---
        # Framework uses the state_delta in the yielded Event's actions.
        # No need to modify ctx.session.state directly here.
        if terminate_conversation:
             logger.info(f"Conversation {session_id} ended. State delta sent for final update.")
             # If explicit clearing is needed, yield another event with only delta:
             clear_actions = EventActions(state_delta={"current_step": "terminated", "status": current_turn_state.get('status')}) # Or specific terminal state
             yield Event(author=agent_name, actions=clear_actions)


        logger.info(f"--- Agent Turn End: Session {session_id} ---")
        logger.debug(f"Final state delta sent for turn: {state_changes}") # Log the delta we sent


# --- Follow-Up Logic (Background Thread - Requires Proactive Send Capability) ---
# (Code for follow_up_checker remains the same, with warnings)
_follow_up_running = True
def follow_up_checker(data_manager_instance: DataManager):
    """Checks CSV for unresponsive leads and logs need for follow-up."""
    logger.info("Follow-up checker thread started.")
    global _follow_up_running
    while _follow_up_running:
        try:
            time.sleep(FOLLOW_UP_CHECK_INTERVAL_SECONDS)
            now = datetime.now()
            cutoff_time = now - timedelta(seconds=SIMULATED_24H_DELAY_SECONDS)
            logger.debug("Running follow-up check...")

            active_leads_for_check = data_manager_instance.get_all_active_leads_for_followup()

            for lead_info in active_leads_for_check:
                session_id = lead_info.get('lead_id')
                ts_str = lead_info.get('last_agent_msg_ts')
                flag_str = lead_info.get('follow_up_sent_flag', 'False')

                if not session_id or not ts_str: continue

                try:
                    last_msg_time = datetime.fromisoformat(ts_str)
                    follow_up_sent = flag_str.lower() == 'true'

                    if not follow_up_sent and last_msg_time < cutoff_time:
                        logger.info(f"Follow-up condition met for {session_id}. Needs proactive message.")

                        followup_message = "Just checking in to see if you're still interested. Let me know when you're ready to continue."
                        logger.warning(f"PROACTIVE SEND NEEDED for {session_id}: '{followup_message}'. Requires ADK function like 'adk.send_message(session_id, ...)'")
                        try:
                            # --- PSEUDOCODE/PLACEHOLDER for sending ---
                            # Check if the function exists before calling
                            # Replace 'adk' as needed
                            # if hasattr(adk, 'send_message_to_session'):
                            #    logging.info(f"Attempting to send follow-up to {session_id} via ADK...")
                            #    # adk.send_message_to_session(session_id, followup_message)
                            #    logging.info(f"Proactive message attempt made for {session_id}.")
                            #    # If successful, update CSV:
                            #    data_manager_instance.update_lead({
                            #        "lead_id": session_id, "follow_up_sent_flag": 'True'
                            #    })
                            # else:
                            #    logger.error("Follow-up failed: ADK function for proactive send not found.")

                            # Simulate marking as sent in CSV for testing flow
                            logger.debug(f"Simulating marking follow-up as sent in CSV for {session_id}")
                            data_manager_instance.update_lead({
                                "lead_id": session_id, "follow_up_sent_flag": 'True'
                            })
                            pass

                        except Exception as send_err:
                             logger.error(f"Error attempting to send/simulate follow-up for {session_id}: {send_err}", exc_info=True)

                except ValueError:
                    if ts_str:
                         logger.warning(f"Could not parse timestamp '{ts_str}' for session {session_id}")
                except Exception as inner_err:
                     logger.error(f"Error processing follow-up check for {session_id}: {inner_err}", exc_info=True)

        except Exception as e:
             logger.error(f"Error in follow-up checker loop: {e}", exc_info=True)
             time.sleep(10)


# --- Main Execution Block ---
if __name__ == "__main__":
    logger.info("--- Setting up Sales Agent Runner ---")

    # --- Initialize Services ---
    data_manager_main = DataManager(filename=CSV_FILENAME)
    session_service_main = InMemorySessionService()

    # --- Create Agent Instance ---
    sales_agent_main = SalesFlowAgent(name="SalesFlowAgent", data_manager=data_manager_main)

    # --- Create Runner ---
    runner_main = Runner(
        agent=sales_agent_main,
        app_name=APP_NAME,
        session_service=session_service_main
    )

    # --- Start Follow-up Thread ---
    follow_up_thread = threading.Thread(target=follow_up_checker, args=(data_manager_main,), daemon=True, name="FollowUpChecker")
    follow_up_thread.start()
    logger.warning("Follow-up checker thread started. Sending follow-up messages depends on ADK proactive capabilities.")

    # --- Simulation / Interaction Loop ---
    print("\n--- Sales Agent Simulation ---")
    print("Enter 'quit' to exit.")
    print("Enter trigger [lead_id] [name] (e.g., trigger L001 Alice) to start a new lead.")
    print("Otherwise, enter [lead_id] [message] (e.g., L001 yes) to respond.")

    try:
        while True:
            user_input = input(">>> ").strip()
            if user_input.lower() == 'quit':
                break

            parts = user_input.split(" ", 2)
            command = parts[0].lower()

            if command == "trigger" and len(parts) == 3:
                lead_id, lead_name = parts[1], parts[2]
                session = session_service_main.get_session(
                    app_name=APP_NAME, user_id="cli_user", session_id=lead_id
                )
                if not session:
                    logger.info(f"Creating new session for trigger: {lead_id}")
                    initial_state = {"name": lead_name}
                    session = session_service_main.create_session(
                         app_name=APP_NAME, user_id="cli_user", session_id=lead_id, state=initial_state
                    )
                    if not session:
                        logger.error(f"Failed to create session {lead_id}")
                        continue
                else:
                     logger.warning(f"Session {lead_id} already exists. Updating name and re-triggering.")
                     # Update name directly in the retrieved session object's state
                     # This assumes the object returned by get_session is mutable enough
                     # for this direct update before the runner uses it.
                     if hasattr(session, 'state') and isinstance(session.state, dict):
                          session.state["name"] = lead_name
                     elif hasattr(session, 'state') and hasattr(session.state, '__setitem__'): # Handle State object
                          session.state["name"] = lead_name


                logger.info(f"Running initial turn for {lead_id}")
                events = runner_main.run(user_id="cli_user", session_id=lead_id, new_message=None)
                for event in events:
                    if event.content and event.content.parts:
                         print(f"Agent ({lead_id}): {event.content.parts[0].text}")

            elif len(parts) >= 2 and command != "trigger":
                lead_id, message_text = parts[0], " ".join(parts[1:])
                session = session_service_main.get_session(
                    app_name=APP_NAME, user_id="cli_user", session_id=lead_id
                )
                if not session:
                    print(f"Error: Session '{lead_id}' not found. Use 'trigger {lead_id} [name]' first.")
                    continue

                user_content = genai_types.Content(role='user', parts=[genai_types.Part(text=message_text)])
                logger.info(f"Running turn for {lead_id} with message: '{message_text}'")
                events = runner_main.run(user_id="cli_user", session_id=lead_id, new_message=user_content)

                has_response = False
                for event in events:
                     logger.debug(f"Event received: {event.model_dump_json(indent=1, exclude_none=True)}")
                     if event.content and event.content.parts:
                         print(f"Agent ({lead_id}): {event.content.parts[0].text}")
                         has_response = True

                if not has_response:
                     final_session = session_service_main.get_session(
                         app_name=APP_NAME, user_id="cli_user", session_id=lead_id
                     )
                     # Safely get state dictionary for logging
                     final_state_dict = {}
                     if final_session and hasattr(final_session.state, 'to_dict'):
                         final_state_dict = final_session.state.to_dict()
                     elif final_session and isinstance(final_session.state, dict):
                         final_state_dict = final_session.state

                     if not final_state_dict or final_state_dict.get("current_step") in ["completed", "declined", "no_response", "terminated"]:
                         print(f"Agent ({lead_id}): (Conversation ended or no response generated)")
                     else:
                         print(f"Agent ({lead_id}): (No message response this turn - state: {final_state_dict.get('current_step')})")

            else:
                if command == "trigger":
                    print(f"Invalid trigger format. Use: 'trigger [id] [name]'")
                else:
                    print("Invalid input. Format: 'trigger [id] [name]' or '[id] [message]' or 'quit'")

    except KeyboardInterrupt:
        print("\nCtrl+C detected. Exiting...")
    finally:
        # --- Cleanup ---
        print("Stopping follow-up thread...")
        _follow_up_running = False
        if 'follow_up_thread' in locals() and follow_up_thread.is_alive():
             follow_up_thread.join(timeout=1.0)
        print("Exited.")