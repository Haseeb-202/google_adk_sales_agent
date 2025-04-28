# agent/sales_agent_logic.py
# Corrected: Explicitly clear timestamp/flag when user responds after declining.

import logging
import threading
import time
import csv
import os
import copy # Import the copy module
from datetime import datetime, timedelta, timezone # Import timezone
from typing import Dict, Any, Optional, List, TypedDict, AsyncGenerator
from threading import Lock # Import Lock

from typing_extensions import override

# --- Attempt to import ADK components ---
try:
    from google.adk.agents import BaseAgent
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions import InMemorySessionService, Session
    from google.adk.runners import Runner
    from google.adk.events import Event, EventActions # Import EventActions
    from google.genai import types as genai_types
    # Import the State class from its actual location (adjust path if needed)
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

# Import DataManager from the same package
from .data_manager import DataManager # Assuming data_manager.py is in the same directory

logger = logging.getLogger(__name__)

# --- Configuration ---
SIMULATED_24H_DELAY_SECONDS = 5 # Seconds for testing follow-up
FOLLOW_UP_CHECK_INTERVAL_SECONDS = 5 # How often the checker runs


# --- Custom Sales Agent ---
class SalesFlowAgent(BaseAgent):
    """Orchestrates the sales lead conversation flow."""

    data_manager: DataManager
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, data_manager: DataManager):
        """Initializes the SalesFlowAgent."""
        # Pass data_manager to super for Pydantic validation
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

        state_obj_or_dict = ctx.session.state
        is_custom_state_obj = hasattr(state_obj_or_dict, '_value') and hasattr(state_obj_or_dict, '_delta')

        user_utterance = ""
        if ctx.session.events:
             last_event = ctx.session.events[-1]
             if last_event.content and last_event.content.role == 'user' and last_event.content.parts:
                 user_utterance = last_event.content.parts[0].text

        # Logging Initial State
        if is_custom_state_obj: logger.info(f"State object at start: {state_obj_or_dict.to_dict()}")
        else: logger.info(f"Initial state dict at start: {state_obj_or_dict}")
        logger.info(f"User utterance: '{user_utterance}'")

        updated_csv_data = {"lead_id": session_id}
        message_to_send = None
        final_goodbye_message = None
        terminate_conversation = False
        timestamp_now = datetime.now(timezone.utc)
        timestamp_now_iso = timestamp_now.isoformat()
        state_changes: Dict[str, Any] = {}

        # Use a local dictionary copy for state manipulation
        current_turn_state = copy.deepcopy(state_obj_or_dict.to_dict() if is_custom_state_obj else state_obj_or_dict)

        # --- Handle New Conversation ---
        if "current_step" not in current_turn_state:
            logger.info(f"Session {session_id} state lacks 'current_step'. Initializing.")
            lead_name = current_turn_state.get("name", f"Lead_{session_id[:6]}")
            logger.info(f"Using lead name: {lead_name}")

            state_changes = {
                "lead_id": session_id, "name": lead_name,
                "current_step": "awaiting_consent", "status": "awaiting_consent",
                "age": None, "country": None, "interest": None,
                "last_agent_msg_ts": timestamp_now_iso, "follow_up_sent": False
            }
            current_turn_state.update(state_changes)

            self.data_manager.update_lead({
                "lead_id": session_id, "name": lead_name, "status": "awaiting_consent",
                "last_agent_msg_ts": timestamp_now_iso, "follow_up_sent_flag": str(False)
            })
            message_to_send = f"Hey {lead_name}, thank you for filling out the form. I'd like to gather some information from you. Is that okay?"

        # --- Handle Existing Conversation ---
        else:
            current_step = current_turn_state.get("current_step", "initial")
            logger.info(f"Existing session {session_id}. Step: {current_step}")

            # Clear timer/flag in local state copy and track if user responded
            # Only clear if user actually said something
            # And only if we are NOT currently awaiting follow-up (don't reset flags if user responds late)
            if user_utterance and current_step not in ["awaiting_followup_after_decline"]:
                current_turn_state['last_agent_msg_ts'] = None; state_changes['last_agent_msg_ts'] = None
                current_turn_state['follow_up_sent'] = False; state_changes['follow_up_sent'] = False

            response_lower = user_utterance.lower().strip()
            next_step = current_step
            next_status = current_turn_state.get('status')

            # --- State Machine Logic ---
            if current_step == "awaiting_consent":
                if any(consent in response_lower for consent in ["yes", "ok", "okay", "sure", "yeah", "yep", "affirmative"]):
                    next_step = "awaiting_age"; next_status = "awaiting_age"
                    message_to_send = "Great! What is your age?"
                else: # Decline Consent
                    next_step = "awaiting_followup_after_decline"
                    next_status = "awaiting_followup_after_decline"
                    message_to_send = "Alright, no problem. Have a great day!"
                    current_turn_state['last_agent_msg_ts'] = timestamp_now_iso
                    state_changes['last_agent_msg_ts'] = timestamp_now_iso
                    current_turn_state['follow_up_sent'] = False # Ensure flag is false initially
                    state_changes['follow_up_sent'] = False
                    terminate_conversation = False # Keep active
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
                     final_goodbye_message = "Ok, goodbye!"
                     terminate_conversation = True
                 else: message_to_send = "Could you please tell me what product or service... interested in?"

            # --- CORRECTED State Handling for response after decline ---
            elif current_step == "awaiting_followup_after_decline":
                 logger.info(f"User responded after declining consent ({session_id}). Terminating.")
                 message_to_send = "Ok, goodbye!"
                 next_step = "declined_final"; next_status = "declined_final"
                 terminate_conversation = True
                 # --- Explicitly clear timestamp/flag for delta ---
                 current_turn_state['last_agent_msg_ts'] = None; state_changes['last_agent_msg_ts'] = None
                 current_turn_state['follow_up_sent'] = False; state_changes['follow_up_sent'] = False
                 # --- End Correction ---

            elif current_step in ["completed", "declined", "no_response", "declined_final", "terminated"]:
                 logger.info(f"Conversation {session_id} already finished...")
                 message_to_send = None; terminate_conversation = True
            else: logger.warning(f"Turn for session {session_id} in unexpected state..."); message_to_send = "Sorry, I seem to have gotten confused..."

            # Apply step/status changes
            if next_step != current_step: current_turn_state['current_step'] = next_step; state_changes['current_step'] = next_step
            if next_status != current_turn_state.get('status'): current_turn_state['status'] = next_status; state_changes['status'] = next_status

        # --- Send Response(s) ---
        event_actions = EventActions()
        if message_to_send:
            logger.info(f"Preparing agent response for {session_id}: '{message_to_send}'")
            agent_content = genai_types.Content(role='model', parts=[genai_types.Part(text=message_to_send)])

            # Update timestamp in local state and track change if needed
            if any(q in message_to_send for q in ["okay?", "age?", "from?", "interested in?", "ready to continue."]) \
               or current_turn_state.get('current_step') == "awaiting_followup_after_decline":
                current_turn_state['last_agent_msg_ts'] = timestamp_now_iso; state_changes['last_agent_msg_ts'] = timestamp_now_iso
                current_turn_state['follow_up_sent'] = False; state_changes['follow_up_sent'] = False

            if state_changes:
                 logger.debug(f"Attaching state delta to event (main message): {state_changes}")
                 event_actions = EventActions(state_delta=copy.deepcopy(state_changes))
                 # state_changes_sent = state_changes # No longer need to track separately
                 state_changes = {} # Reset delta after preparing main event actions

            yield Event(author=agent_name, content=agent_content, actions=event_actions)


        # --- Send the final goodbye message ---
        if final_goodbye_message and final_goodbye_message != message_to_send:
             logger.info(f"Preparing final goodbye for {session_id}: '{final_goodbye_message}'")
             goodbye_content = genai_types.Content(role='model', parts=[genai_types.Part(text=final_goodbye_message)])
             final_status = current_turn_state.get('status', 'terminated')
             # Ensure final status/step are included in delta
             state_changes["current_step"] = "terminated"
             state_changes["status"] = final_status
             final_actions = EventActions(state_delta=copy.deepcopy(state_changes)) # Use copy
             yield Event(author=agent_name, content=goodbye_content, actions=final_actions)
             state_changes = {}


        # --- Update CSV ---
        updated_csv_data["status"] = current_turn_state.get('status', 'unknown')
        updated_csv_data["last_agent_msg_ts"] = current_turn_state.get('last_agent_msg_ts', '')
        updated_csv_data["follow_up_sent_flag"] = str(current_turn_state.get('follow_up_sent', False))
        if current_turn_state.get('status') == 'secured':
             updated_csv_data["age"] = current_turn_state.get('age', '')
             updated_csv_data["country"] = current_turn_state.get('country', '')
             updated_csv_data["interest"] = current_turn_state.get('interest', '')
        logger.info(f"Updating CSV for {session_id}. Status: {updated_csv_data['status']}")
        self.data_manager.update_lead(updated_csv_data)

        # --- Handle Conversation Termination Event Action ---
        if terminate_conversation and not final_goodbye_message:
             logger.info(f"Conversation {session_id} ended without explicit goodbye. Yielding termination event.")
             final_status = current_turn_state.get('status', 'terminated')
             clear_state_delta = {"current_step": "terminated", "status": final_status}
             clear_state_delta.update(state_changes) # Include any pending changes
             clear_actions = EventActions(state_delta=clear_state_delta)
             yield Event(author=agent_name, actions=clear_actions) # Event with no content, only actions
             state_changes = {}


        logger.info(f"--- Agent Turn End: Session {session_id} ---")
        if state_changes: logger.debug(f"State delta pending (unsent with message this turn): {state_changes}")


# --- Follow-Up Logic (Background Thread) ---
_follow_up_running = True
def follow_up_checker(data_manager_instance: DataManager, pending_followups: Dict[str, str], followup_lock: Lock):
    """Checks CSV for unresponsive leads and adds messages to pending_followups."""
    logger.info("Follow-up checker thread started.")
    global _follow_up_running
    while _follow_up_running:
        try:
            time.sleep(FOLLOW_UP_CHECK_INTERVAL_SECONDS)
            now = datetime.now(timezone.utc)
            cutoff_time = now - timedelta(seconds=SIMULATED_24H_DELAY_SECONDS)
            logger.debug(f"Running follow-up check... Cutoff Time (UTC): {cutoff_time.isoformat()}")

            active_leads_for_check = data_manager_instance.get_all_active_leads_for_followup()

            for lead_info in active_leads_for_check:
                session_id = lead_info.get('lead_id')
                ts_str = lead_info.get('last_agent_msg_ts')
                flag_str = lead_info.get('follow_up_sent_flag', 'False')
                current_status = lead_info.get('status')

                if not session_id or not ts_str: continue

                try:
                    last_msg_time = datetime.fromisoformat(ts_str)
                    if last_msg_time.tzinfo is None:
                         last_msg_time = last_msg_time.replace(tzinfo=timezone.utc)

                    follow_up_sent = flag_str.lower() == 'true'
                    time_diff = now - last_msg_time
                    is_overdue = last_msg_time < cutoff_time

                    logger.debug(
                        f"Checker[{session_id}]: Status={current_status}, Now={now.isoformat()}, "
                        f"Cutoff={cutoff_time.isoformat()}, LastMsg={last_msg_time.isoformat()}, "
                        f"Diff={time_diff.total_seconds():.1f}s, ConfigDelay={SIMULATED_24H_DELAY_SECONDS}s, "
                        f"Flag={follow_up_sent}, Overdue={is_overdue}"
                    )

                    if not follow_up_sent and is_overdue:
                        logger.info(f"Follow-up condition met for {session_id} (Status: {current_status}). Preparing action.")

                        final_status_after_followup = current_status
                        terminate_after_followup = False
                        if current_status == "awaiting_followup_after_decline":
                             final_status_after_followup = "declined_final"
                             terminate_after_followup = True

                        followup_message = "Just checking in to see if you're still interested. Let me know when you're ready to continue."

                        # --- Update CSV FIRST ---
                        update_data = {"lead_id": session_id, "follow_up_sent_flag": 'True'}
                        if terminate_after_followup:
                             update_data["status"] = final_status_after_followup
                             update_data["last_agent_msg_ts"] = ''

                        try:
                            data_manager_instance.update_lead(update_data)
                            logger.info(f"Updated CSV for {session_id}: follow_up_sent=True, status={update_data.get('status', current_status)}")

                            # --- Add message to pending queue AFTER successful CSV update ---
                            with followup_lock:
                                 current_flag_val = data_manager_instance.get_lead(session_id).get('follow_up_sent_flag', 'False') if data_manager_instance.get_lead(session_id) else 'False'
                                 if current_flag_val.lower() == 'true' and session_id not in pending_followups:
                                     pending_followups[session_id] = followup_message
                                     logger.info(f"Added pending follow-up message for {session_id}")
                                 else:
                                     logger.debug(f"Follow-up for {session_id} was already pending or flag update failed.")

                            logger.warning(f"PROACTIVE SEND NEEDED for {session_id}: Requires ADK function.")

                        except Exception as update_err:
                             logger.error(f"Error updating CSV during follow-up for {session_id}: {update_err}", exc_info=True)


                except ValueError:
                    if ts_str: logger.warning(f"Could not parse timestamp '{ts_str}' for session {session_id}")
                except Exception as inner_err: logger.error(f"Error processing follow-up check for {session_id}: {inner_err}", exc_info=True)

        except Exception as e: logger.error(f"Error in follow-up checker loop: {e}", exc_info=True); time.sleep(10)


# --- Main Execution Block (Not run when using app.py) ---
if __name__ == '__main__':
    logger.warning("This script ('sales_agent_logic.py') is not intended to be run directly.")
    logger.warning("Run 'app.py' to start the Flask web server and simulation.")
    pass