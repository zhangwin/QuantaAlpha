import React, { useState, useEffect } from 'react';
import { HomePage } from '@/pages/HomePage';
import { MiningDashboardPage } from '@/pages/MiningDashboardPage';
import { FactorLibraryPage } from '@/pages/FactorLibraryPage';
import { BacktestPage } from '@/pages/BacktestPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { Layout } from '@/components/layout/Layout';
import type { PageId } from '@/components/layout/Layout';
import { ParticleBackground } from '@/components/ParticleBackground';
import { TaskProvider, useTaskContext } from '@/context/TaskContext';

// Inner component to access context
const AppContent: React.FC = () => {
  // Check localStorage synchronously so we immediately show dashboard if a task was in progress
  const savedMiningTaskId = localStorage.getItem('quantaalpha_mining_task_id');
  const savedBacktestTaskId = localStorage.getItem('quantaalpha_backtest_task_id');
  const initialPage: PageId = savedMiningTaskId ? 'mining_dashboard' : savedBacktestTaskId ? 'backtest' : 'home';
  const [currentPage, setCurrentPage] = useState<PageId>(initialPage);
  const { miningTask, backtestTask } = useTaskContext();

  // Auto-switch to dashboard when a task newly appears (restore completed or new start)
  useEffect(() => {
    if (miningTask && currentPage === 'home') {
       setCurrentPage('mining_dashboard');
    }
  }, [miningTask?.taskId]);

  useEffect(() => {
    if (backtestTask && currentPage === 'home') {
      setCurrentPage('backtest');
    }
  }, [backtestTask?.taskId]);

  return (
    <>
      <ParticleBackground />
      {/*
        Use display:none to hide non-current pages instead of conditional unmounting.
        This ensures that components are not unmounted when switching pages, so WebSocket/task state is not lost.
      */}
      <div style={{ display: currentPage === 'home' ? 'block' : 'none' }}>
        <HomePage onNavigate={setCurrentPage} />
      </div>
      <div style={{ display: currentPage === 'mining_dashboard' ? 'block' : 'none' }}>
        <MiningDashboardPage onNavigate={setCurrentPage} />
      </div>
      <div style={{ display: currentPage === 'library' ? 'block' : 'none' }}>
        <Layout currentPage={currentPage} onNavigate={setCurrentPage}>
          <FactorLibraryPage />
        </Layout>
      </div>
      <div style={{ display: currentPage === 'backtest' ? 'block' : 'none' }}>
        <Layout currentPage={currentPage} onNavigate={setCurrentPage}>
          <BacktestPage />
        </Layout>
      </div>
      <div style={{ display: currentPage === 'settings' ? 'block' : 'none' }}>
        <Layout currentPage={currentPage} onNavigate={setCurrentPage}>
          <SettingsPage />
        </Layout>
      </div>
    </>
  );
};

export const App: React.FC = () => {
  return (
    <TaskProvider>
      <AppContent />
    </TaskProvider>
  );
};
