// static/script.js
document.addEventListener('DOMContentLoaded', () => {
    const messageForm = document.getElementById('message-form');
    const messageInput = document.getElementById('message-input');
    const chatTranscript = document.getElementById('chat-transcript');
    const errorMessageDiv = document.getElementById('error-message');

    // Function to add a message to the transcript DIV
    function addMessageToTranscript(author, text) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', author.toLowerCase()); // agent or user

        const authorSpan = document.createElement('span');
        authorSpan.classList.add('author');
        authorSpan.textContent = `${author}:`;

        const textSpan = document.createElement('span');
        textSpan.classList.add('text');
        // Use textContent for security (prevents HTML injection)
        textSpan.textContent = text;

        messageDiv.appendChild(authorSpan);
        messageDiv.appendChild(textSpan);
        chatTranscript.appendChild(messageDiv);

        // Scroll to the bottom smoothly after adding a message
        chatTranscript.scrollTo({ top: chatTranscript.scrollHeight, behavior: 'smooth' });
    }

    // Handle message submission via the form
    messageForm.addEventListener('submit', async (event) => {
        event.preventDefault(); // Prevent default page reload
        const userText = messageInput.value.trim();
        errorMessageDiv.textContent = ''; // Clear previous errors

        if (!userText) {
            return; // Don't send empty messages
        }

        // Display user message immediately in the transcript
        addMessageToTranscript('User', userText);
        const originalInputValue = userText; // Keep original value in case of error
        messageInput.value = ''; // Clear input field
        messageInput.disabled = true; // Disable input while waiting for agent response

        try {
            // Send message to Flask backend endpoint /send_message
            const response = await fetch('/send_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: userText }), // Send message in JSON body
            });

            // Check if the HTTP response status is OK
            if (!response.ok) {
                let errorMsg = `HTTP error! status: ${response.status}`;
                try {
                     // Try to get more specific error from JSON response
                    const errorData = await response.json();
                    errorMsg = errorData.error || errorMsg;
                } catch (e) {
                    // Ignore if response wasn't JSON
                }
                throw new Error(errorMsg);
            }

            // Parse the JSON response from the server
            const data = await response.json();

            // Display agent response(s) received from the backend
            if (data.responses && data.responses.length > 0) {
                data.responses.forEach(msg => {
                    addMessageToTranscript(msg.author, msg.text);
                });
            } else {
                // Log if agent provided no specific message response this turn
                console.log("Agent provided no message response this turn.");
                // Optionally, add a system message to the transcript
                // addMessageToTranscript('System', '(Agent did not provide a message this turn)');
            }

        } catch (error) {
            // Handle errors during fetch or response processing
            console.error('Error sending message:', error);
            errorMessageDiv.textContent = `Error: ${error.message}`;
            // Add error message to transcript for user visibility
            addMessageToTranscript('System', `Error: ${error.message}`);
            // Restore input field content if sending failed
            messageInput.value = originalInputValue;
        } finally {
             // Always re-enable input field and focus it
             messageInput.disabled = false;
             messageInput.focus();
        }
    });

     // --- Ensures transcript is scrolled to bottom on initial page load ---
     // Needs to run slightly after initial rendering allows scroll height calculation
     setTimeout(() => {
        chatTranscript.scrollTop = chatTranscript.scrollHeight;
     }, 100); // Small delay

     // Focus the input field when the page loads
     messageInput.focus();
});