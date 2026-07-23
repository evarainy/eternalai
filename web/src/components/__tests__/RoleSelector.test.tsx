import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import RoleSelector from '../RoleSelector';
import { useRoleStore } from '../../stores/roleStore';

describe('RoleSelector', () => {
  beforeEach(() => {
    localStorage.clear();
    useRoleStore.setState({ roles: [] });
  });

  it('updates the role store and identifies the claim as unverified', async () => {
    render(<RoleSelector />);

    expect(screen.getByText(/未认证声明/)).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '角色声明' }));
    const adminOptions = await screen.findAllByText('admin');
    fireEvent.click(adminOptions.at(-1)!);

    await waitFor(() => {
      expect(useRoleStore.getState().roles).toEqual(['admin']);
    });
  });
});
