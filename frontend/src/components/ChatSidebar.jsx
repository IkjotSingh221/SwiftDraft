import React, { useState, useEffect } from 'react';
import { Button } from './ui/button';
import { Plus, MessageSquare, Menu, X } from 'lucide-react';

// Mock data for demo
const mockSessions = [
  {
    id: '1',
    title: 'Machine Learning Research Methods',
    created_at: '2024-01-15T10:00:00Z',
    updated_at: '2024-01-15T14:30:00Z'
  },
  {
    id: '2',
    title: 'Climate Change Literature Review',
    created_at: '2024-01-14T09:15:00Z',
    updated_at: '2024-01-14T16:45:00Z'
  },
  {
    id: '3',
    title: 'Quantum Computing Applications',
    created_at: '2024-01-13T11:30:00Z',
    updated_at: '2024-01-13T15:20:00Z'
  },
  {
    id: '4',
    title: 'Data Analysis Methodology',
    created_at: '2024-01-12T08:45:00Z',
    updated_at: '2024-01-12T17:10:00Z'
  },
  {
    id: '5',
    title: 'Neural Networks in Healthcare',
    created_at: '2024-01-11T13:20:00Z',
    updated_at: '2024-01-11T18:30:00Z'
  }
];

export function ChatSidebar({ 
  isOpen, 
  onToggle, 
  currentSessionId, 
  onSessionSelect, 
  onNewChat 
}) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadSessions();
  }, []);

  const loadSessions = async () => {
    setLoading(true);
    // Simulate loading delay
    await new Promise(resolve => setTimeout(resolve, 500));
    setSessions(mockSessions);
    setLoading(false);
  };

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div 
          className="fixed inset-0 bg-black bg-opacity-50 z-40 lg:hidden"
          onClick={onToggle}
        />
      )}

      {/* Sidebar */}
      <div className={`
        fixed top-0 left-0 h-full bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 z-50 transition-transform duration-300 ease-in-out
        ${isOpen ? 'translate-x-0' : '-translate-x-full'}
        lg:relative lg:translate-x-0 w-80
      `}>
        <div className="flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800">
            <h2 className="font-semibold text-gray-900 dark:text-white">Chat History</h2>
            <button
              onClick={onToggle}
              className="lg:hidden p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              <X size={20} className="text-gray-500 dark:text-gray-400" />
            </button>
          </div>

          {/* New Chat Button */}
          <div className="p-4 border-b border-gray-200 dark:border-gray-800">
            <Button
              onClick={onNewChat}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white"
            >
              <Plus size={16} className="mr-2" />
              New Chat
            </Button>
          </div>

          {/* Sessions List */}
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {loading ? (
              <div className="text-center py-8">
                <div className="animate-spin w-6 h-6 border-2 border-indigo-600 border-t-transparent rounded-full mx-auto"></div>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">Loading sessions...</p>
              </div>
            ) : sessions.length > 0 ? (
              sessions.map((session) => (
                <button
                  key={session.id}
                  onClick={() => onSessionSelect(session.id, session.title)}
                  className={`
                    w-full text-left p-3 rounded-lg transition-colors group
                    ${currentSessionId === session.id
                      ? 'bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800'
                      : 'hover:bg-gray-50 dark:hover:bg-gray-800'
                    }
                  `}
                >
                  <div className="flex items-start space-x-3">
                    <MessageSquare size={16} className={`
                      mt-0.5 flex-shrink-0
                      ${currentSessionId === session.id
                        ? 'text-indigo-600 dark:text-indigo-400'
                        : 'text-gray-400 dark:text-gray-500'
                      }
                    `} />
                    <div className="min-w-0 flex-1">
                      <p className={`
                        text-sm font-medium truncate
                        ${currentSessionId === session.id
                          ? 'text-indigo-900 dark:text-indigo-100'
                          : 'text-gray-900 dark:text-white'
                        }
                      `}>
                        {session.title}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        {new Date(session.updated_at).toLocaleDateString()}
                      </p>
                    </div>
                  </div>
                </button>
              ))
            ) : (
              <div className="text-center py-8">
                <MessageSquare size={32} className="text-gray-300 dark:text-gray-600 mx-auto mb-3" />
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  No conversations yet.
                </p>
                <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                  Start a new chat to begin!
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Mobile menu button */}
      <button
        onClick={onToggle}
        className="lg:hidden fixed top-4 left-4 z-30 p-2 bg-white dark:bg-gray-900 rounded-lg shadow-lg border border-gray-200 dark:border-gray-800"
      >
        <Menu size={20} className="text-gray-600 dark:text-gray-300" />
      </button>
    </>
  );
}
