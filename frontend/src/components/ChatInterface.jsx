import React, { useState, useRef, useEffect } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Send, Loader2 } from 'lucide-react';
import { ChatMessage } from './ChatMessage';

// Mock data for existing sessions
const mockSessionMessages = {
  '1': [
    {
      id: '1-1',
      content: 'I need help with machine learning research methods for my thesis.',
      role: 'user',
      timestamp: '2024-01-15T10:00:00Z'
    },
    {
      id: '1-2',
      content: "I'd be happy to help you with machine learning research methods! Let me break down the key aspects for your thesis...",
      role: 'assistant',
      timestamp: '2024-01-15T10:02:00Z'
    }
  ],
  '2': [
    {
      id: '2-1',
      content: 'Can you help me structure a literature review on climate change impacts?',
      role: 'user',
      timestamp: '2024-01-14T09:15:00Z'
    },
    {
      id: '2-2',
      content: "Absolutely! Here's a comprehensive structure for your climate change literature review...",
      role: 'assistant',
      timestamp: '2024-01-14T09:18:00Z'
    }
  ]
};

export function ChatInterface({ sessionId, sessionTitle, onSessionCreated }) {
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    if (sessionId) {
      loadMessages();
    } else {
      setMessages([]);
    }
  }, [sessionId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const loadMessages = async () => {
    if (!sessionId) return;

    setLoadingMessages(true);
    await new Promise(resolve => setTimeout(resolve, 500));

    const sessionMessages = mockSessionMessages[sessionId] || [];
    setMessages(sessionMessages);
    setLoadingMessages(false);
  };

  const generateAIResponse = (userMessage) => {
    const responses = [
      "I'd be happy to help you with your research! Let me break down the key aspects...",
      "Excellent question! For academic research, I suggest structuring your approach as follows...",
      "This is a fascinating research direction! Let me provide you with a structured approach..."
    ];

    return responses[Math.floor(Math.random() * responses.length)];
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!inputValue.trim()) return;

    const userMessage = inputValue.trim();
    setInputValue('');
    setLoading(true);

    try {
      let currentSessionId = sessionId;

      if (!currentSessionId) {
        const title = userMessage.length > 50 
          ? userMessage.substring(0, 50) + '...' 
          : userMessage;
        currentSessionId = Date.now().toString();
        onSessionCreated(currentSessionId, title);
      }

      const newUserMessage = {
        id: Date.now().toString(),
        content: userMessage,
        role: 'user',
        timestamp: new Date().toISOString()
      };

      setMessages(prev => [...prev, newUserMessage]);

      await new Promise(resolve => setTimeout(resolve, 1000 + Math.random() * 2000));

      const aiResponse = generateAIResponse(userMessage);
      const newAiMessage = {
        id: (Date.now() + 1).toString(),
        content: aiResponse,
        role: 'assistant',
        timestamp: new Date().toISOString()
      };

      setMessages(prev => [...prev, newAiMessage]);
    } catch (error) {
      console.error('Error sending message:', error);
    } finally {
      setLoading(false);
    }
  };

  const getWelcomeMessage = () => {
    if (sessionId && messages.length === 0 && !loadingMessages) {
      return (
        <div className="text-center py-12">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-emerald-100 text-emerald-600 rounded-full mb-4">
            <Send size={24} />
          </div>
          <h3 className="text-lg font-medium text-gray-900 mb-2">Continue Your Research</h3>
          <p className="text-gray-600 max-w-md mx-auto">
            Welcome back to "{sessionTitle}". Let's continue working on your research project.
          </p>
        </div>
      );
    }

    if (!sessionId) {
      return (
        <div className="text-center py-12">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-indigo-100 text-indigo-600 rounded-full mb-4">
            <Send size={24} />
          </div>
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            Welcome back, ready to draft your next research paper?
          </h3>
          <p className="text-gray-600 max-w-md mx-auto">
            Start a conversation to begin your research journey. I can help with literature reviews, 
            methodology design, data analysis, and LaTeX formatting.
          </p>
        </div>
      );
    }

    return null;
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-4 py-6">
          {loadingMessages ? (
            <div className="text-center py-12">
              <div className="animate-spin w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full mx-auto mb-4"></div>
              <p className="text-gray-600">Loading conversation...</p>
            </div>
          ) : messages.length === 0 ? (
            getWelcomeMessage()
          ) : (
            <div className="space-y-4">
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
            </div>
          )}

          {loading && (
            <div className="flex space-x-3 p-4 rounded-lg bg-gray-50 mr-8 mt-4">
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-emerald-600 text-white flex items-center justify-center">
                <Loader2 size={18} className="animate-spin" />
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-900 mb-1">
                  Research Assistant
                </p>
                <div className="flex space-x-1">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="border-t border-gray-200 bg-white">
        <div className="max-w-4xl mx-auto p-4">
          <form onSubmit={handleSubmit} className="flex space-x-3">
            <div className="flex-1">
              <Input
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder="Ask about research methods, literature reviews, data analysis..."
                disabled={loading}
                className="w-full"
              />
            </div>
            <Button
              type="submit"
              disabled={!inputValue.trim() || loading}
              className="bg-indigo-600 hover:bg-indigo-700 text-white px-6"
            >
              {loading ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
            </Button>
          </form>
          <div className="text-xs text-gray-500 text-center mt-2">
            Research Assistant can make mistakes. Consider checking important information.
          </div>
        </div>
      </div>
    </div>
  );
}
