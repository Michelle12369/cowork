import { render, screen } from '@testing-library/react';
import StepChain from './StepChain';
import type { StepItem } from '@/types';

const makeStep = (stepKey: string, title: string, status: StepItem['status']): StepItem => ({
  stepKey,
  title,
  description: null,
  status,
});

test('renders PENDING step title', () => {
  render(<StepChain steps={[makeStep('s1', 'Pending Step', 'PENDING')]} />);
  expect(screen.getByText('Pending Step')).toBeInTheDocument();
});

test('renders RUNNING step title', () => {
  render(<StepChain steps={[makeStep('s2', 'Running Step', 'RUNNING')]} />);
  expect(screen.getByText('Running Step')).toBeInTheDocument();
});

test('renders SUCCESS step title', () => {
  render(<StepChain steps={[makeStep('s3', 'Success Step', 'SUCCESS')]} />);
  expect(screen.getByText('Success Step')).toBeInTheDocument();
});

test('renders ERROR step title', () => {
  render(<StepChain steps={[makeStep('s4', 'Error Step', 'ERROR')]} />);
  expect(screen.getByText('Error Step')).toBeInTheDocument();
});

test('renders dynamic step with d* key alongside static steps', () => {
  const steps: StepItem[] = [
    makeStep('s1', 'Static Step', 'SUCCESS'),
    makeStep('d1', 'Dynamic Step A', 'RUNNING'),
    makeStep('d2', 'Dynamic Step B', 'PENDING'),
  ];
  render(<StepChain steps={steps} />);
  expect(screen.getByText('Static Step')).toBeInTheDocument();
  expect(screen.getByText('Dynamic Step A')).toBeInTheDocument();
  expect(screen.getByText('Dynamic Step B')).toBeInTheDocument();
});
