import React, { useState } from 'react';
import { ChatSidebar } from './ChatSidebar';
import { ChatInterface } from './ChatInterface';
import { ProfileDropdown } from './ProfileDropdown';
import { BookOpen } from 'lucide-react';

export function Dashboard() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [currentSessionTitle, setCurrentSessionTitle] = useState('New Chat');

  const handleSessionSelect = (sessionId, title) => {
    setCurrentSessionId(sessionId);
    setCurrentSessionTitle(title);
    setSidebarOpen(false); // Close sidebar on mobile after selection
  };

  const handleNewChat = () => {
    setCurrentSessionId(null);
    setCurrentSessionTitle('New Chat');
    setSidebarOpen(false); // Close sidebar on mobile
  };

  const handleSessionCreated = (sessionId, title) => {
    setCurrentSessionId(sessionId);
    setCurrentSessionTitle(title);
  };

  return (
    <div className="h-screen bg-white dark:bg-gray-900 flex">
      {/* Sidebar */}
      <ChatSidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        currentSessionId={currentSessionId}
        onSessionSelect={handleSessionSelect}
        onNewChat={handleNewChat}
      />

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 lg:px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3 lg:ml-0 ml-12">
              <div className="flex items-center space-x-2">
                <BookOpen size={24} className="text-indigo-600 dark:text-indigo-400" />
                <h1 className="text-lg font-semibold text-gray-900 dark:text-white">
                  Research Assistant
                </h1>
              </div>
            </div>

            <div className="flex items-center space-x-4">
              <div className="hidden sm:block">
                <span className="text-sm text-gray-600 dark:text-gray-400">
                  {currentSessionTitle}
                </span>
              </div>
              <ProfileDropdown />
            </div>
          </div>
        </header>

        {/* Chat Interface */}
        <div className="flex-1 overflow-hidden">
          <ChatInterface
            sessionId={currentSessionId}
            sessionTitle={currentSessionTitle}
            onSessionCreated={handleSessionCreated}
          />
        </div>
      </div>
    </div>
  );
}
