import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { McpServersPage } from "../pages/McpServersPage";
import {
  useMcpServers,
  useCreateMcpServer,
  useUpdateMcpServer,
  useDeleteMcpServer,
} from "../hooks/useMcpServers";
import type { McpServerListResponse } from "../api/types";

vi.mock("../hooks/useMcpServers");

function makeServer(overrides: Partial<McpServerListResponse["servers"][0]> = {}) {
  return {
    id: "srv-1",
    name: "wiki",
    url: "http://127.0.0.1:9210/mcp",
    enabled: true,
    has_api_key: false,
    ...overrides,
  };
}

function mockServersResponse(servers: McpServerListResponse["servers"]): McpServerListResponse {
  return { servers };
}

describe("McpServersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders server list with url, status and secret fact", () => {
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([
        makeServer({ id: "s1", name: "wiki", url: "http://localhost:9210/mcp" }),
        makeServer({
          id: "s2",
          name: "build",
          url: "https://tools.example.com/mcp",
          enabled: false,
          has_api_key: true,
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({} as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({} as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({} as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    expect(screen.getByText("wiki")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:9210/mcp")).toBeInTheDocument();
    expect(screen.getByText("включён")).toBeInTheDocument();
    expect(screen.getByText("build")).toBeInTheDocument();
    expect(screen.getByText("отключён")).toBeInTheDocument();
    expect(screen.getByText("секрет задан")).toBeInTheDocument();
    expect(screen.getByText("без секрета")).toBeInTheDocument();
  });

  it("shows empty state when no servers", () => {
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({} as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({} as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({} as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    expect(screen.getByTestId("mcp-servers-empty")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    vi.mocked(useMcpServers).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useMcpServers>);

    render(<McpServersPage />);

    expect(screen.getByTestId("mcp-servers-loading")).toBeInTheDocument();
  });

  it("shows error state", () => {
    vi.mocked(useMcpServers).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
    } as ReturnType<typeof useMcpServers>);

    render(<McpServersPage />);

    expect(screen.getByTestId("mcp-servers-error")).toBeInTheDocument();
  });

  it("creates a server from the modal", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeServer());
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({} as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({} as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    fireEvent.click(screen.getByTestId("mcp-servers-add"));
    fireEvent.change(screen.getByTestId("mcp-server-create-name"), {
      target: { value: "wiki" },
    });
    fireEvent.change(screen.getByTestId("mcp-server-create-url"), {
      target: { value: "http://localhost:9210/mcp" },
    });
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        name: "wiki",
        url: "http://localhost:9210/mcp",
        api_key: null,
        enabled: true,
      });
    });
  });

  it("toggles server enabled state", () => {
    const mutate = vi.fn();
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([makeServer({ id: "s1", name: "wiki", enabled: true })]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({} as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({} as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    fireEvent.click(screen.getByTestId("mcp-server-toggle-wiki"));
    expect(mutate).toHaveBeenCalledWith({ serverId: "s1", body: { enabled: false } });
  });

  it("edits server url from the modal", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeServer());
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([makeServer({ id: "s1", name: "wiki" })]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({} as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({} as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    fireEvent.click(screen.getByText("Изменить"));
    const urlInput = screen.getByTestId("mcp-server-edit-url");
    fireEvent.change(urlInput, { target: { value: "https://new.example.com/mcp" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        serverId: "s1",
        body: { url: "https://new.example.com/mcp" },
      });
    });
  });

  it("deletes server after confirmation", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ deleted: true });
    vi.mocked(useMcpServers).mockReturnValue({
      data: mockServersResponse([makeServer({ id: "s1", name: "wiki" })]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMcpServers>);
    vi.mocked(useCreateMcpServer).mockReturnValue({} as ReturnType<typeof useCreateMcpServer>);
    vi.mocked(useUpdateMcpServer).mockReturnValue({} as ReturnType<typeof useUpdateMcpServer>);
    vi.mocked(useDeleteMcpServer).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteMcpServer>);

    render(<McpServersPage />);

    fireEvent.click(screen.getByText("Удалить"));
    // После открытия модалки текста «Удалить» два (карточка + подтверждение)
    const confirmButtons = screen.getAllByText("Удалить");
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("s1");
    });
  });
});
