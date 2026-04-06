import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'

const routes = [
  { path: '/',           redirect: '/channels' },
  { path: '/login',      component: () => import('../views/LoginView.vue'),       meta: { public: true } },
  { path: '/channels',   component: () => import('../views/ChannelsView.vue') },
  { path: '/channels/:id', component: () => import('../views/ChannelView.vue') },
  { path: '/actors',     component: () => import('../views/ActorsView.vue') },
  { path: '/actors/:id', component: () => import('../views/ActorView.vue') },
  { path: '/invitations', component: () => import('../views/InvitationsView.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return '/login'
  }
})

export default router
