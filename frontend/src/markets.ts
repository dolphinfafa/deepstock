export const markets = [
  { value: 'US', label: '美股' },
  { value: 'CN', label: 'A股' },
  { value: 'Both', label: 'Both' },
] as const

export function marketLabel(value: string): string {
  return markets.find((market) => market.value === value)?.label || value
}
