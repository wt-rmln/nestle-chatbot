// static/app.js

class Chatbox {
    constructor() {
        this.args = {
            openButton: document.querySelector('.chatbox__button'),
            chatBox:   document.querySelector('.chatbox__support'),
            sendButton: document.querySelector('.send__button'),
        }

        this.state = false;
        this.messages = [
          { name: "Smartie",  message: "Hey! I’m Smartie, your personal MadeWithNestlé assistant. Ask me anything, and I’ll quickly search the entire site to find the answers you need!" }
        ];
    }

    display() {
        const { openButton, chatBox, sendButton } = this.args;
        this.updateChatText(chatBox);

        openButton.addEventListener('click', () => this.toggleState(chatBox));
        sendButton.addEventListener('click', () => this.onSendButton(chatBox));
        
        const inputNode = chatBox.querySelector('input');
        inputNode.addEventListener("keyup", ({ key }) => {
            if (key === "Enter") {
                this.onSendButton(chatBox);
            }
        });
    }

    toggleState(chatbox) {
        this.state = !this.state;
        chatbox.classList.toggle('chatbox--active', this.state);
    }

    onSendButton(chatbox) {
        const textField = chatbox.querySelector('input');
        const text1 = textField.value.trim();
        if (!text1) return;

        this.messages.push({ name: "User", message: text1 });
        this.updateChatText(chatbox);

        textField.value = '';

        const typingIdx = this.messages.push({ name: "Sam", message: "..." }) - 1;
        this.updateChatText(chatbox);

        fetch('/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text1 })
        })
        .then(r => r.json())
        .then(r => {
            this.messages[typingIdx].message = r.answer;
            this.updateChatText(chatbox);
        })
        .catch(error => {
            console.error('Error:', error);
            this.messages[typingIdx].message = "Error...";
            this.updateChatText(chatbox);
        });
    }

    updateChatText(chatbox) {
        const container = chatbox.querySelector('.chatbox__messages');
        container.innerHTML = '';

        this.messages.forEach(item => {
            const msgDiv = document.createElement('div');
            msgDiv.classList.add(
                'messages__item',
                item.name === "User"
                  ? 'messages__item--visitor'
                  : 'messages__item--operator'
            );

            // msgDiv.textContent = item.message;
            const urlRegex = /(https?:\/\/[^\s]+)/g;
            const htmlWithLinks = item.message.replace(urlRegex, '<a href="$1" target="_blank">$1</a>');
            msgDiv.innerHTML = htmlWithLinks;

            container.prepend(msgDiv);
        });
    }
}

const chatbox = new Chatbox();
chatbox.display();
