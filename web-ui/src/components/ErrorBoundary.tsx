import { AlertTriangle, RefreshCcw, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Đổi giá trị này thì lỗi được xóa mà không remount children — dùng cho pathname. */
  resetKey?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
  resetKey?: string;
}

/**
 * Không có ranh giới lỗi thì một throw khi render ở bất kỳ đâu cũng cho ra màn
 * hình trắng, không thông báo, không lối thoát — Studio chạy local cả buổi nên
 * đó là kiểu hỏng tệ nhất.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  static getDerivedStateFromProps(
    props: ErrorBoundaryProps,
    state: ErrorBoundaryState,
  ): Partial<ErrorBoundaryState> | null {
    if (props.resetKey === state.resetKey) return null;
    // Rời khỏi màn hình hỏng là đủ để thử lại; không cần remount cả cây con.
    return { error: null, resetKey: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Studio hỏng khi render:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="page state-page" role="alert">
        <AlertTriangle size={28} />
        <h1>Giao diện gặp lỗi</h1>
        <p>{error.message || "Lỗi không xác định khi dựng màn hình."}</p>
        <div className="state-page-actions">
          {/* Dựng lại đủ để thoát khi lỗi đến từ một snapshot dữ liệu vừa được
              thay thế; tải lại trang là lối thoát cho phần còn lại. */}
          <button className="secondary-button" onClick={() => this.setState({ error: null })}>
            <RotateCcw size={16} />Thử dựng lại
          </button>
          <button className="secondary-button" onClick={() => window.location.reload()}>
            <RefreshCcw size={16} />Tải lại trang
          </button>
        </div>
      </div>
    );
  }
}
