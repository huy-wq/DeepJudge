import {
  createRouter,
  createWebHistory,
  type Router,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'

import { notifyError } from '../feedback'
import { consumeSessionError, ensureSession, setUnauthorizedNavigator } from '../session'
// 这个页面必须在主包里（不走懒加载）：懒加载失败时没有它，回退页自己也加载不出来
import LoadErrorView from '../views/LoadErrorView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    /** 不需要登录即可访问；默认 false（守卫优先拒绝） */
    public?: boolean
  }
}

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/load-error',
    name: 'load-error',
    component: LoadErrorView,
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../layouts/AdminLayout.vue'),
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('../views/HomeView.vue'),
      },
      {
        path: 'rubrics',
        name: 'rubrics',
        component: () => import('../views/RubricListView.vue'),
      },
      {
        path: 'grading',
        name: 'grading',
        component: () => import('../views/GradingView.vue'),
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('../views/ReviewQueueView.vue'),
      },
      {
        // 数字 id 才走详情，避免与 /review 抢路由
        path: 'review/:id(\\d+)',
        name: 'review-detail',
        component: () => import('../views/ReviewDetailView.vue'),
      },
      {
        path: 'rubrics/new',
        name: 'rubric-new',
        component: () => import('../views/RubricEditorView.vue'),
      },
      {
        // 数字 id 才走编辑页，避免与 /rubrics/new 抢路由
        path: 'rubrics/:id(\\d+)',
        name: 'rubric-edit',
        component: () => import('../views/RubricEditorView.vue'),
      },
    ],
  },
  {
    // 未知路径也要先过登录检查：未登录时不能因为"路径不存在"就看到管理端骨架
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFoundView.vue'),
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()): Router {
  const router = createRouter({ history, routes })

  // 页面内任意接口 401 时立刻回登录页，而不是等下一次导航
  setUnauthorizedNavigator(() => {
    const current = router.currentRoute.value
    if (current.name === 'login' || current.name === 'load-error') {
      return
    }
    void router.replace({
      name: 'login',
      query: { redirect: current.fullPath },
    })
  })

  let recovering = false
  router.onError((error) => {
    // 懒加载 chunk 失败（断网、发版后旧标签页）不会经过组件生命周期，
    // ErrorBoundary 也接不到；没有这一步就是一个纯白页面。
    notifyError(error)
    if (recovering || router.currentRoute.value.name === 'load-error') {
      return
    }
    recovering = true
    void router
      .replace({ name: 'load-error' })
      .finally(() => {
        recovering = false
      })
  })

  router.beforeEach(async (to) => {
    const authenticated = await ensureSession()
    const sessionError = consumeSessionError()
    if (sessionError) {
      notifyError(sessionError)
    }

    if (to.meta.public) {
      return authenticated && to.name === 'login' ? { name: 'home' } : true
    }
    if (!authenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    return true
  })

  return router
}
