# Google ADK Sales Agent (Flask Web Application)

## Project Overview

This project implements a conversational sales agent designed to handle multiple leads concurrently, guide them through a qualification process, store data, and manage follow-ups. It uses a Google Agent Development Kit (ADK) structure (specifically `BaseAgent`, `Runner`, `InvocationContext`) integrated within a Flask web application for user interaction.

The agent follows a predefined flow: asking for consent, then sequentially collecting age, country, and product interest. Data for each lead is persisted in a `leads.csv` file. A background thread monitors conversations and queues follow-up messages for unresponsive leads (including those who initially decline consent), which are then displayed to the user via client-side polling.

## Features

*   **Web Interface:** Provides a simple web UI (built with Flask and basic HTML/CSS/JS) for lead triggering and conversation interaction.
*   **Conversational Flow:** Implements the required sequence: Consent -> Age -> Country -> Interest.
*   **ADK `BaseAgent` Structure:** Core logic encapsulated in `SalesFlowAgent` inheriting from `BaseAgent`.
*   **ADK Session State:** Uses the ADK's `InMemorySessionService` and `ctx.session.state`, persisting changes via `state_delta` in yielded `Event` objects.
*   **Concurrent Session Handling:** The underlying ADK components and session service manage distinct conversation states for different `lead_id`s. (Note: True parallel request handling depends on the WSGI server used for deployment).
*   **CSV Data Persistence:** Lead details (`lead_id`, `name`, `age`, `country`, `interest`, `status`) and follow-up metadata are stored in `leads.csv`.
*   **Thread-Safe CSV:** `DataManager` class uses `threading.Lock` for safe concurrent writes to `leads.csv`.
*   **Follow-Up Mechanism:**
    *   Handles follow-ups for leads stalled during questioning *and* for leads who initially decline consent.
    *   Uses a background thread (`follow_up_checker`) to monitor `leads.csv` for timeouts based on `last_agent_msg_ts`.
    *   Uses a simulated delay (`SIMULATED_24H_DELAY_SECONDS`) for testing.
    *   Queues follow-up messages in a server-side dictionary (`pending_followups`).
    *   Frontend polls (`/check_followup` endpoint) to retrieve and display queued follow-up messages.
*   **Server-Side Chat History:** Chat transcripts are stored in server memory (`chat_histories` dictionary) to persist across page refreshes (but not server restarts).


## Setup Instructions

1.  **Clone Repository:**
    ```bash
    git clone <your-repository-url>
    cd google_adk_sales_agent
    ```

2.  **Create Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # Activate the environment
    # Linux/macOS:
    source venv/bin/activate
    # Windows (Command Prompt/PowerShell):
    venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    *   **Confirm ADK Package Name:** Check the official documentation for the specific Google ADK library that provides `google.adk.agents`, `google.adk.sessions`, etc.
    *   **Edit `requirements.txt`:** Replace the placeholder line starting with `google-adk>=...` with the correct package name found in the ADK documentation.
    *   **Install:**
        ```bash
        pip install -r requirements.txt
        ```

4.  **Authentication (If Required by ADK):**
    *   While this specific agent logic doesn't call external authenticated Google APIs *directly*, the underlying ADK framework might.
    *   If you encounter authentication errors, try authenticating your environment using the Google Cloud CLI:
        ```bash
        gcloud auth application-default login
        ```
    *   Refer to the specific ADK documentation for its authentication needs.

5.  **Environment Variables (Optional):**
    *   If needed, create a `.env` file in the root directory for settings like `FLASK_SECRET_KEY`.

## Running the Application

1.  Make sure your virtual environment is activated.
2.  Run the Flask application from the project's root directory:
    ```bash
    flask run
    # Or: python app.py
    ```
3.  Flask will start a development server, typically accessible at `http://127.0.0.1:5000`. Open this address in your web browser.

## Usage Guide

1.  **Start:** Navigate to the application URL in your browser (e.g., `http://127.0.0.1:5000`). You will see the "Sales Agent Lead Capture" form.
2.  **Trigger Lead:** Enter a unique **Lead ID** (e.g., `LEAD001`) and a **Lead Name** (e.g., `Alice`). Click "Start Conversation".
3.  **Chat:** You will be redirected to the chat interface. The agent's initial greeting should appear. Type your responses in the input box at the bottom and press Enter or click "Send".
4.  **Concurrency:** Open another browser tab/window, navigate to the application URL again, and trigger a *different* lead (e.g., `LEAD002`, `Bob`). You can now switch between tabs and interact with both leads independently.
5.  **Follow-up Test:**
    *   Start a lead (e.g., `LEAD_STALL`).
    *   Answer the first question (e.g., `yes`).
    *   Wait for the agent to ask the next question (e.g., "What is your age?").
    *   **Do not answer.** Wait longer than the simulated delay (default 30s) + polling interval (default 7s).
    *   The follow-up message ("Just checking in...") should appear automatically in the chat window for `LEAD_STALL` after the delay.
    *   You can also test the decline follow-up by saying "no" to the initial consent and waiting.
6.  **Exit:** Close the browser tabs. Stop the Flask server in the terminal by pressing `Ctrl+C`.

## Code Explanation

*   **`app.py`:** The Flask web server.
    *   Initializes Flask, ADK components (`Runner`, `SessionService`), `DataManager`, and the `SalesFlowAgent`.
    *   Starts the `follow_up_checker` background thread.
    *   Defines routes:
        *   `/`: Shows the lead form (`index.html`).
        *   `/start_chat`: Handles form submission, creates/resets ADK session and server-side history, runs the agent's first turn, stores initial messages, redirects to `/chat`. Uses Flask session cookie to store the user's current `lead_id`.
        *   `/chat`: Displays the chat UI (`chat.html`), retrieving the conversation history from the server-side dictionary based on the `lead_id` in the Flask session.
        *   `/send_message`: Receives user messages (via JavaScript `fetch`), runs the corresponding agent turn via the `Runner`, appends user/agent messages to the server-side history, and returns agent responses as JSON.
        *   `/check_followup`: Endpoint polled by JavaScript. Checks a shared dictionary (`pending_followups`) populated by the background thread, returning any queued follow-up message for the user's `lead_id`.
    *   Manages server-side chat history (`chat_histories`) and pending follow-ups (`pending_followups`) using Python dictionaries and `threading.Lock` for safety.
*   **`agent/sales_agent_logic.py`:** Contains the core agent.
    *   `SalesFlowAgent`: Inherits `BaseAgent`. `_run_async_impl` contains the state machine logic. It gets session state (`ctx.session.state`), determines the next step based on the current step and user input, calculates necessary state changes (`state_changes`), yields `Event` objects containing agent text (`content`) and state updates (`actions.state_delta`). Uses `DataManager` to update CSV. Handles the `awaiting_followup_after_decline` state.
    *   `follow_up_checker`: Function run in a background thread. Reads `leads.csv` via `DataManager`, checks timestamps/flags, identifies overdue leads, and adds the follow-up message to the shared `pending_followups` dictionary (protected by a lock passed from `app.py`). It *simulates* updating the follow-up flag in the CSV after queuing.
*   **`agent/data_manager.py`:** Class responsible for all thread-safe interactions with `leads.csv` using a `threading.Lock`. Reads and writes lead data including status and follow-up metadata. Filters leads for the checker thread.
*   **`templates/` & `static/`:** Standard Flask structure for HTML templates and static files (CSS, JS).
    *   `script.js`: Handles form submission for sending messages, dynamically updates the chat transcript, and polls the `/check_followup` endpoint periodically to display follow-up messages.

## Design Decisions

*   **Framework:** Chose Flask for its simplicity in creating the web interface and handling requests.
*   **ADK Structure:** Adopted the `BaseAgent`/`Runner`/`InvocationContext`/`Event` structure based on provided examples, assuming this reflects the target ADK.
*   **State Management:**
    *   **Conversational State:** Utilized the ADK's `ctx.session.state` and the `state_delta` mechanism within yielded `Events` for turn-to-turn persistence, as inferred from the `State` and `EventActions` classes. A local copy (`current_turn_state`) is used within `_run_async_impl` for easier manipulation before calculating the final delta.
    *   **Chat History:** Stored server-side in a Python dictionary (`chat_histories`) keyed by `lead_id` to persist across page refreshes. Protected by a `Lock`. *Limitation: Lost on server restart.*
    *   **Follow-up Queue:** Used a server-side dictionary (`pending_followups`) and `Lock` for the background thread to communicate needed follow-ups to the main Flask process serving the polling endpoint.
*   **Data Persistence:** Used `leads.csv` as mandated by requirements. Encapsulated all CSV logic in a thread-safe `DataManager` class using `threading.Lock`.
*   **Concurrency:** Relied on the ADK `Runner`'s presumed internal concurrency (threads/asyncio) and ensured the shared `DataManager` was thread-safe. Used `threaded=True` in `app.run` for development testing (a production server like Gunicorn is needed for true concurrent request handling).
*   **Follow-up Implementation:** Due to the lack of a clear ADK mechanism for proactive server-to-client pushes in this context, a client-side polling approach (`/check_followup`) was implemented. The background thread identifies needed follow-ups from the CSV, queues them server-side, and the frontend periodically asks if a message is waiting. The thread simulates the CSV flag update, acknowledging this isn't ideal but necessary for the detection loop.

## Known Limitations

*   **Follow-up Sending:** The `follow_up_checker` thread *detects* when follow-ups are needed but relies on client-side polling (`/check_followup`) rather than a direct server push (like WebSockets) to display the message. The simulation also relies on the thread updating the CSV flag, which ideally the agent logic should handle upon successful message delivery confirmation (which isn't possible here). True proactive sending requires different frontend/backend communication or specific ADK features.
*   **Server-Side Storage Volatility:** Chat history and pending follow-ups are stored in memory and lost if the Flask server restarts. A database or external cache (like Redis) would be needed for persistence.
*   **Concurrency in Development:** The Flask development server (`flask run`) typically handles requests serially, not truly concurrently. Testing high concurrency requires a production WSGI server (e.g., Gunicorn, Waitress).
*   **ADK Specifics:** The implementation assumes the behavior of the imported ADK components based on examples. The exact behavior of state persistence via `state_delta` and the existence of proactive messaging APIs should be verified against the official documentation for the specific ADK library version used.
*   **Error Handling:** Error handling is basic and could be made more robust.
