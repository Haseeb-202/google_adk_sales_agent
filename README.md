
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
    *   **Find the correct ADK package name:** Check the official documentation for the specific Google ADK library you installed that provides `google.adk.agents`, `google.adk.sessions`, etc.
    *   **Edit `requirements.txt`:** Replace the placeholder line `# google-adk>=1.0.0 # Placeholder name...` with the actual package name and version (if known). For example, if the package is `g-corp-adk`, the line might just be `g-corp-adk`.
    *   **Install:**
        ```bash
        pip install -r requirements.txt
        ```
        This will install the specified ADK library, `google-generativeai` (used for message types), `pydantic` (likely dependency), and `python-dotenv` (optional).

4.  **Authentication (If Required by ADK):**
    *   Although this agent doesn't directly call LLMs, the underlying ADK framework *might* require Google Cloud authentication.
    *   If you encounter authentication errors during startup, try authenticating your environment using the Google Cloud CLI:
        ```bash
        gcloud auth application-default login
        ```
    *   Consult the specific ADK documentation for detailed authentication requirements.

5.  **Environment Variables (Optional):**
    *   If the ADK requires API keys or specific configurations, create a `.env` file in the root directory and add them (e.g., `GOOGLE_API_KEY=your_key`). The script uses `python-dotenv` to load these if the file exists. Remember to add `.env` to your `.gitignore`.

## Running the Simulation

1.  Make sure your virtual environment is activated.
2.  Run the main script from the project's root directory:
    ```bash
    python sales_agent_adk_structured.py
    ```
3.  The script will start, initialize the components, and present a command-line prompt `>>>`.

## Usage Guide (Simulation Commands)

Interact with the agent simulation via the command line:

*   **Start a new conversation:**
    ```
    >>> trigger [lead_id] [name]
    ```
    Example: `>>> trigger L001 Alice`
    Example: `>>> trigger CUST50 Bobert Smith`

*   **Send a message as a lead:**
    ```
    >>> [lead_id] [message]
    ```
    Example: `>>> L001 yes please`
    Example: `>>> CUST50 35`
    Example: `>>> L001 I am interested in cloud storage`

*   **Exit the simulation:**
    ```
    >>> quit
    ```

**Observe:**
*   Watch the command line for messages printed by the agent.
*   Monitor the console output for INFO and DEBUG logs showing the agent's internal state transitions.
*   Check the `leads.csv` file (created in the same directory) to see how lead data and statuses are updated.

## Testing

*   **Happy Path:** Run a lead through the entire flow (`trigger -> yes -> age -> country -> interest`) and verify the final status in `leads.csv` is "secured".
*   **Consent Decline:** Trigger a lead and respond with "no" or similar. Verify the agent responds appropriately and the status in `leads.csv` is "no_response".
*   **Concurrency:** Trigger multiple leads (e.g., L001, L002) quickly. Interleave messages for them (`L001 yes`, `L002 ok`, `L001 30`, `L002 25`, etc.). Verify that each conversation proceeds independently and the data in `leads.csv` is correct for each lead.
*   **Invalid Input:** Provide non-numeric input when asked for age. Verify the agent re-prompts correctly.
*   **Follow-up Simulation:** Trigger a lead, answer one or two questions, then wait longer than `SIMULATED_24H_DELAY_SECONDS` (default 30s) + `FOLLOW_UP_CHECK_INTERVAL_SECONDS` (default 5s). Observe the logs for the `Follow-up condition met...` message and the `PROACTIVE SEND NEEDED...` warning. Check that the `follow_up_sent_flag` in `leads.csv` is updated to `True` (due to the simulation placeholder). Then, send a message for that lead and verify the conversation continues correctly.

## Design Decisions

*   **Agent Structure:** Utilized `google.adk.agents.BaseAgent` as inspired by example ADK structures, implementing the core logic within `_run_async_impl`.
*   **State Management:** Leveraged the ADK's session state (`ctx.session.state`) assuming it uses the custom `State` object internally. State changes are calculated within the turn and explicitly included in the `state_delta` field of the `EventActions` attached to the yielded `Event`, relying on the ADK framework to persist these deltas.
*   **Data Persistence:** Implemented a thread-safe `DataManager` class using `threading.Lock` to handle reads/writes to the mandatory `leads.csv` file. This ensures integrity during concurrent agent turns.
*   **Concurrency Handling:** Relies on the ADK `Runner` to manage concurrent execution of `_run_async_impl` for different sessions. Thread-safety for shared resources (CSV file) is handled explicitly.
*   **Follow-up:** Implemented via a separate background thread checking the CSV file. This approach was chosen due to the apparent lack of a documented proactive messaging API in the specific ADK examples referenced. It has known limitations in accessing real-time session state and depends heavily on ADK capabilities for actually *sending* the follow-up message. Timestamps and flags are stored in the CSV to facilitate this check.
*   **Simulation:** Included an interactive command-line loop within the `if __name__ == "__main__":` block for easy local testing and demonstration without needing a separate UI or complex setup.

## Known Limitations / Future Improvements

*   **Follow-up Sending:** The background thread can only *detect* when a follow-up is needed based on CSV data. Actually sending the message requires a specific proactive messaging function from the ADK library, which is currently represented by a placeholder and a warning log. If the ADK provides such a function, the `follow_up_checker` needs to be updated to call it.
*   **Error Handling:** Error handling is basic. Production code would need more robust handling for CSV I/O errors, ADK API errors, unexpected user input, etc.
*   **Lead Name Trigger:** The simulation assumes the lead name is passed during the initial `trigger` command. How this name is provided in a real deployment depends on the actual ADK trigger mechanism.
*   **ADK Package Name:** The specific `pip` package name for the ADK library needs to be confirmed and updated in `requirements.txt`.
