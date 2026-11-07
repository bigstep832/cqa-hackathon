import { useEffect, useState } from 'react';

type Resource<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; error: unknown };

export function useOperatorResource<T>(read: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Resource<T>>({ status: 'loading' });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    read(controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ status: 'ready', data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, [read, revision]);
  return { state, retry: () => { setState({ status: 'loading' }); setRevision(value => value + 1); } };
}
