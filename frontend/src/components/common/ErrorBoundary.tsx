import React from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: React.ReactNode;
  /** Custom fallback rendered when a child throws. Defaults to a full "Failed to load"
   *  panel with a Reload button; pass `null` for a silent/local degrade (e.g. an optional
   *  widget that should just disappear rather than blast the whole page). */
  fallback?: React.ReactNode;
}

interface State {
  error: Error | null;
}

class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render(): React.ReactNode {
    const { error } = this.state;
    const { fallback } = this.props;
    if (error !== null) {
      if (fallback !== undefined) {
        return fallback;
      }
      return (
        <div className="flex h-full items-center justify-center p-8">
          <Result
            status="error"
            title="Failed to load"
            subTitle={error.message}
            extra={
              <Button type="primary" onClick={() => window.location.reload()}>
                Reload
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
