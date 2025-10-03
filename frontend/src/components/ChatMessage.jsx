import React, { useState } from 'react';
import { User, Bot, Copy, Check } from 'lucide-react';

export function ChatMessage({ message }) {
  const [copied, setCopied] = useState(false);

  const copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  const isUser = message.role === 'user';

  return (
    <div className={`
      flex space-x-3 p-4 rounded-lg transition-colors
      ${isUser 
        ? 'bg-indigo-50 dark:bg-indigo-900/20 ml-8' 
        : 'bg-gray-50 dark:bg-gray-800/50 mr-8'
      }
    `}>
      <div className={`
        flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center
        ${isUser 
          ? 'bg-indigo-600 text-white' 
          : 'bg-emerald-600 text-white'
        }
      `}>
        {isUser ? <User size={18} /> : <Bot size={18} />}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <p className="text-sm font-medium text-gray-900 dark:text-white">
            {isUser ? 'You' : 'Research Assistant'}
          </p>
          <div className="flex items-center space-x-2">
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {new Date(message.timestamp).toLocaleTimeString()}
            </span>
            {!isUser && (
              <button
                onClick={copyToClipboard}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="Copy message"
              >
                {copied ? (
                  <Check size={14} className="text-green-600" />
                ) : (
                  <Copy size={14} className="text-gray-500 dark:text-gray-400" />
                )}
              </button>
            )}
          </div>
        </div>
        
        <div className="prose prose-sm max-w-none text-gray-700 dark:text-gray-300">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    </div>
  );
}
