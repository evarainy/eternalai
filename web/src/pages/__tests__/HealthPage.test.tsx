import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import HealthPage from '../HealthPage';

describe('HealthPage', () => {
  it('displays health status ok', () => {
    render(
      <MemoryRouter>
        <HealthPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/ok/i)).toBeInTheDocument();
  });
});
